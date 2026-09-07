import json

from .db import mode, query
from .model import embed

LEXICAL_LIMIT = 125


def _visibility(user: dict) -> list[str]:
    return [
        user["scope"],
        "all",
        *(["sf", "london", "operators"] if user["role"] == "operator" else []),
    ]


async def lexical(
    text: str, user: dict, *, limit: int = LEXICAL_LIMIT, incidents: bool = True
) -> list[dict]:
    """The lexical half of retrieval: visibility-scoped full-text ranking, no embedding call."""
    return (
        await query(
            "SELECT * FROM sources WHERE visibility=ANY($1) AND created_at<=now() AND ((kind IN ('article','case') AND status='approved') OR (kind='incident' AND $3)) ORDER BY ts_rank(to_tsvector('english',title||' '||body),plainto_tsquery('english',$2)) DESC,id LIMIT $4",
            [_visibility(user), text, incidents, limit],
        )
    ).rows


async def retrieve(text: str, user: dict) -> list[dict]:
    rows = await lexical(text, user)
    if mode() == "demo":
        return rows
    vector = await embed(text)
    semantic = (
        await query(
            "SELECT * FROM sources WHERE visibility=ANY($1) AND kind IN ('article','case') AND status='approved' AND created_at<=now() AND embedding IS NOT NULL ORDER BY embedding <=> $2::vector LIMIT 5",
            [_visibility(user), json.dumps(vector)],
        )
    ).rows
    return list({s["id"]: s for s in [*semantic, *rows]}.values())
