import json
from pathlib import Path

import pytest

from relay.config import validate_environment


def test_cloud_validation_rejects_missing_secrets_and_insecure_origin(monkeypatch):
    for name in [
        "DATABASE_URL",
        "SESSION_SECRET",
        "APP_ORIGIN",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "JIRA_CONFIG_JSON",
    ]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        validate_environment(cloud=True)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/test")
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    for name in [
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_EMBEDDING_MODEL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_CONFIG_JSON",
    ]:
        monkeypatch.setenv(name, "test-only")
    monkeypatch.setenv("APP_ORIGIN", "http://localhost:3000")
    with pytest.raises(ValueError, match="HTTPS origin"):
        validate_environment(cloud=True)


def test_production_runtime_is_python_and_static_ui_only():
    docker = Path("Dockerfile").read_text()
    runtime = docker.split(" AS runtime", 1)[1]
    assert "relay.web" in runtime
    assert "/app/node_modules" not in runtime
    assert "scripts/deploy-web.ts" not in runtime
    assert (
        json.loads(Path("package.json").read_text())["scripts"]["worker"]
        == "uv run python -m relay.worker"
    )
