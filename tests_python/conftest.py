import os
from uuid import uuid4

import psycopg
import pytest_asyncio
from psycopg import sql

from relay import db


@pytest_asyncio.fixture
async def isolated_db(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url or not psycopg.conninfo.conninfo_to_dict(url).get("dbname", "").endswith("_test"):
        raise RuntimeError("Set TEST_DATABASE_URL to a separate database ending in _test")
    schema = "relay_test_" + uuid4().hex
    await db.close_pool()
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as admin:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("APP_MODE", "demo")
        monkeypatch.setenv("SESSION_SECRET", "test-session-secret-with-at-least-32-characters")
        monkeypatch.setenv("APP_ORIGIN", "http://127.0.0.1:3000")
        monkeypatch.setenv("RELAY_DB_OPTIONS", f"-c search_path={schema},public")
        await db.migrate()
        try:
            yield
        finally:
            await db.close_pool()
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
