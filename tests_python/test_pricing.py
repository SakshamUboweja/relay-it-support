"""Cost estimates are comparisons, never invented: unknown prices yield None, not zero."""

import json

import pytest

from relay.agents.pricing import cost, load_pricing, pricing_version
from relay.agents.schemas import Usage

PRICE_ENV = (
    "OPENAI_PRICE_INPUT_PER_M",
    "OPENAI_PRICE_CACHED_INPUT_PER_M",
    "OPENAI_PRICE_OUTPUT_PER_M",
)


@pytest.fixture(autouse=True)
def clean_prices(monkeypatch):
    for name in PRICE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_shipped_pricing_file_and_version():
    pricing = load_pricing()
    assert pricing_version() == "2026-09-06-openrouter"
    assert pricing["models"]["gpt-5.6-terra"] == {
        "inputPerMillion": 2.0,
        "cachedInputPerMillion": 0.2,
        "outputPerMillion": 12.0,
    }


def test_cost_formula_on_a_hand_computed_example():
    usage = Usage(input=1000, output=100, cached=200, reasoning=50)
    # (1000 - 200) * 2.0 + 200 * 0.2 + 100 * 12.0 = 2840 per million tokens.
    assert cost("gpt-5.6-terra", usage) == 0.00284
    assert cost("gpt-5.6-terra", usage.model_dump()) == 0.00284
    assert cost("gpt-5.6-terra", Usage()) == 0.0


def test_unknown_model_or_missing_price_is_none_never_zero(monkeypatch):
    assert cost("unpriced-model", Usage(input=1, output=1)) is None
    monkeypatch.setenv("OPENAI_PRICE_INPUT_PER_M", "1")
    assert cost("unpriced-model", Usage(input=1, output=1)) is None
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_M", "3")
    assert cost("unpriced-model", Usage(input=1_000_000, output=1_000_000)) == 4.0
    assert cost("unpriced-model", Usage(input=1_000_000, output=1_000_000, cached=10)) is None


def test_env_overrides_apply_to_the_configured_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_M", "24")
    assert load_pricing()["models"]["gpt-5.6-terra"]["outputPerMillion"] == 24.0
    assert load_pricing()["models"]["gpt-5.6-terra"]["inputPerMillion"] == 2.0
    assert cost("gpt-5.6-terra", Usage(output=1_000_000)) == 24.0
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_M", "free")
    with pytest.raises(ValueError, match="OPENAI_PRICE_OUTPUT_PER_M"):
        load_pricing()


def test_the_price_file_is_parsed_once_and_reread_when_it_changes(monkeypatch, tmp_path):
    import os
    from pathlib import Path

    from relay.agents import pricing

    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"version": "test-1", "models": {}}))
    monkeypatch.setattr(pricing, "PRICING_PATH", path)
    reads = []
    original = Path.read_text

    def counted(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    assert load_pricing()["version"] == "test-1"
    assert load_pricing()["version"] == "test-1"
    assert len(reads) == 1
    path.write_text(json.dumps({"version": "test-2", "models": {}}))
    os.utime(path, (1e9, 1e9))
    assert load_pricing()["version"] == "test-2"
    assert len(reads) == 2


def test_env_overrides_never_mutate_the_cached_price_file(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_PRICE_OUTPUT_PER_M", "99")
    assert load_pricing()["models"]["gpt-5.6-terra"]["outputPerMillion"] == 99.0
    monkeypatch.delenv("OPENAI_PRICE_OUTPUT_PER_M")
    assert load_pricing()["models"]["gpt-5.6-terra"]["outputPerMillion"] == 12.0
