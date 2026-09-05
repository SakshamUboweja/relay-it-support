"""Best-effort known-pattern sanitation, not a universal DLP classifier."""

import re


def sanitize(text: str) -> str:
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED API KEY]", text)
    text = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]+)\b", "[REDACTED TOKEN]", text
    )
    text = re.sub(
        r"\b(password|passwd|api[ _-]?key|access[ _-]?token|authorization|recovery[ _-]?code)\s*(?:is\s+|[:=]\s*)[^\s,;]+",
        r"\1: [REDACTED]",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(MFA|verification|one.time|OTP|backup)\s*(?:code)?\s*(?:is|:|=)?\s*\d{6,8}\b",
        r"\1 code [REDACTED]",
        text,
        flags=re.I,
    )
    return re.sub(r"Bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", text, flags=re.I)
