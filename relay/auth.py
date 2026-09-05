import hashlib
import hmac
import os
import secrets

from .db import mode, query


def secret() -> str:
    value = os.getenv("SESSION_SECRET", "")
    if len(value) < 32 or (mode() == "live" and value.startswith("local-demo")):
        raise ValueError("Set SESSION_SECRET to at least 32 random characters")
    return value


def demo_cookie(user_id: str) -> str:
    signature = hmac.new(secret().encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return f"{user_id}.{signature}"


async def authenticate(req):
    raw = req.cookies.get("relay_session")
    if mode() == "demo":
        if not raw:
            user_id = "maya"
        else:
            user_id, _, signature = raw.partition(".")
            expected = demo_cookie(user_id).partition(".")[2]
            if not hmac.compare_digest(signature.encode(), expected.encode()):
                raise ValueError("Unauthorized")
    else:
        secret()
        if not raw:
            raise ValueError("Unauthorized")
        found = await query(
            "SELECT user_id FROM sessions WHERE token_hash=$1 AND expires_at>now()",
            [hashlib.sha256(raw.encode()).hexdigest()],
        )
        user_id = found.rows[0]["user_id"] if found.rows else None
    if not user_id:
        raise ValueError("Unauthorized")
    users = await query("SELECT * FROM users WHERE id=$1", [user_id])
    if not users.rows:
        raise ValueError("Unauthorized")
    return users.rows[0]


def assert_origin(req):
    if req.headers.get("origin") != os.getenv("APP_ORIGIN", "http://127.0.0.1:3000"):
        raise ValueError("Forbidden origin")


async def issue_session(user_id: str) -> str:
    secret()
    token = secrets.token_hex(32)
    await query(
        "INSERT INTO sessions VALUES($1,$2,now()+interval '8 hours')",
        [hashlib.sha256(token.encode()).hexdigest(), user_id],
    )
    return token


async def validate_session_token(token: str) -> bool:
    return (
        await query(
            "SELECT 1 FROM sessions WHERE token_hash=$1 AND expires_at>now()",
            [hashlib.sha256(token.encode()).hexdigest()],
        )
    ).rowcount == 1
