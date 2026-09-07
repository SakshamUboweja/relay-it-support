import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


def validate_environment(cloud: bool = False) -> None:
    from .db import mode

    current = mode()
    if not os.getenv("DATABASE_URL"):
        raise ValueError("Missing DATABASE_URL")
    secret = os.getenv("SESSION_SECRET", "")
    if len(secret) < 32 or (current == "live" and secret.startswith("local-demo")):
        raise ValueError("Set SESSION_SECRET to at least 32 random characters")
    from .agents import build_pipeline, select_pipeline

    build_pipeline(select_pipeline(), extract=None)
    if cloud:
        if current != "live":
            raise ValueError("Cloud deployment requires APP_MODE=live.")
        for key in [
            "APP_ORIGIN",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "OPENAI_EMBEDDING_MODEL",
            "JIRA_EMAIL",
            "JIRA_API_TOKEN",
            "JIRA_CONFIG_JSON",
        ]:
            if not os.getenv(key):
                raise ValueError(f"Missing deployment variable: {key}")
        origin = os.environ["APP_ORIGIN"]
        parts = urlsplit(origin)
        if (
            parts.scheme != "https"
            or not parts.netloc
            or parts.path
            or parts.query
            or parts.fragment
            or parts.username
        ):
            raise ValueError("APP_ORIGIN must be an HTTPS origin without a path.")
        from .connector import JiraConfig
        from .model import model_settings

        try:
            JiraConfig.parse(json.loads(os.environ["JIRA_CONFIG_JSON"]))
        except Exception as exc:
            raise ValueError("JIRA_CONFIG_JSON must contain a valid Jira mapping.") from exc
        model_settings()
