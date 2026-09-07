"""Token prices for cost estimates; a missing price yields None rather than a false zero."""

import functools
import json
import os
from pathlib import Path

from ..config import ROOT
from .schemas import Usage

PRICING_PATH = ROOT / "config/pricing.json"
ENV_PRICES = {
    "inputPerMillion": "OPENAI_PRICE_INPUT_PER_M",
    "cachedInputPerMillion": "OPENAI_PRICE_CACHED_INPUT_PER_M",
    "outputPerMillion": "OPENAI_PRICE_OUTPUT_PER_M",
}


@functools.lru_cache(maxsize=8)
def _read_pricing(path: str, mtime: float) -> dict:
    """Parsed once per file version: `cost` is called for every step of every run."""
    return json.loads(Path(path).read_text())


def load_pricing(model: str | None = None) -> dict:
    """The shipped price list, with environment overrides applied to the configured model."""
    shipped = _read_pricing(str(PRICING_PATH), PRICING_PATH.stat().st_mtime)
    pricing = {**shipped, "models": {**shipped["models"]}}
    model = model or os.getenv("OPENAI_MODEL")
    overrides = {}
    for key, name in ENV_PRICES.items():
        raw = os.getenv(name)
        if raw:
            try:
                overrides[key] = float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number of USD per million tokens.") from None
    if model and overrides:
        pricing["models"][model] = {**pricing["models"].get(model, {}), **overrides}
    return pricing


def pricing_version() -> str:
    return load_pricing()["version"]


def cost(model: str, usage: Usage | dict) -> float | None:
    prices = load_pricing(model)["models"].get(model)
    if not prices:
        return None
    counts = usage if isinstance(usage, dict) else usage.model_dump()
    input_tokens, output_tokens = counts.get("input", 0), counts.get("output", 0)
    cached = counts.get("cached", 0)
    per_input, per_output = prices.get("inputPerMillion"), prices.get("outputPerMillion")
    per_cached = prices.get("cachedInputPerMillion") if cached else 0.0
    if per_input is None or per_output is None or per_cached is None:
        return None
    return round(
        ((input_tokens - cached) * per_input + cached * per_cached + output_tokens * per_output)
        / 1e6,
        6,
    )
