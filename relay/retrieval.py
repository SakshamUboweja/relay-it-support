import json

from .db import mode, query
from .model import embed


async def retrieve(text: str, user: dict) -> list[dict]:
    visibility = [
        user["scope"],
        "all",
        *(["sf", "london", "operators"] if user["role"] == "operator" else []),
    ]
    lexical = (
        await query(
            "SELECT * FROM sources WHERE visibility=ANY($1) AND created_at<=now() AND ((kind IN ('article','case') AND status='approved') OR kind='incident') ORDER BY ts_rank(to_tsvector('english',title||' '||body),plainto_tsquery('english',$2)) DESC,id LIMIT 125",
            [visibility, text],
        )
    ).rows
    if mode() == "demo":
        return lexical
    vector = await embed(text)
    semantic = (
        await query(
            "SELECT * FROM sources WHERE visibility=ANY($1) AND kind IN ('article','case') AND status='approved' AND created_at<=now() AND embedding IS NOT NULL ORDER BY embedding <=> $2::vector LIMIT 5",
            [visibility, json.dumps(vector)],
        )
    ).rows
    return list({s["id"]: s for s in [*semantic, *lexical]}.values())
