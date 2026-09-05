"""Small async PostgreSQL adapter retaining the existing schema and SQL contracts."""

import os
import re
import json
from datetime import date, datetime
from uuid import UUID
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .config import ROOT  # also loads the local environment without overriding deployment variables

_pool: AsyncConnectionPool | None = None


def mode() -> str:
    value = os.getenv("APP_MODE", "demo")
    if value not in ("demo", "live"):
        raise ValueError("APP_MODE must be demo or live")
    return value


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        options = os.getenv("RELAY_DB_OPTIONS", "")
        _pool = AsyncConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=8,
            open=False,
            timeout=5,
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "connect_timeout": 5,
                **({"options": options} if options else {}),
            },
        )
        await _pool.open()
        await _pool.wait(timeout=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection():
    async with (await open_pool()).connection() as db:
        yield db


@asynccontextmanager
async def transaction():
    async with connection() as db:
        async with db.transaction():
            yield db


@dataclass
class Result:
    rows: list[dict[str, Any]]
    rowcount: int


async def query(sql: str, params=(), db=None) -> Result:
    # Numbered bind references remain parameters; no values are interpolated into SQL.
    indexes = []

    def bind(match):
        indexes.append(int(match.group(1)) - 1)
        return "%s"

    source = sql.replace("%", "%%") if re.search(r"\$\d+", sql) else sql
    prepared = re.sub(r"\$(\d+)", bind, source)
    values = tuple(
        Jsonb(params[i], dumps=lambda v: json.dumps(v, default=_json_default))
        if isinstance(params[i], dict)
        else params[i]
        for i in indexes
    )
    if db is None:
        async with connection() as conn:
            return await _execute(conn, prepared, values)
    return await _execute(db, prepared, values)


async def _execute(db, sql, values):
    cursor = await db.execute(sql, values or None)
    rows = await cursor.fetchall() if cursor.description else []
    return Result(
        [
            {
                k: _json_default(v) if isinstance(v, (UUID, date, datetime)) else v
                for k, v in row.items()
            }
            for row in rows
        ],
        cursor.rowcount,
    )


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported JSON type: {type(value).__name__}")


async def migrate():
    async with connection() as db:
        await db.execute((ROOT / "migrations/001_initial.sql").read_text())
