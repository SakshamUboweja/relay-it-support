import asyncio

import pytest

from relay import worker
from relay.db import Result, query


@pytest.mark.usefixtures("isolated_db")
async def test_database_parameter_reordering_literals_and_json():
    assert (await query("SELECT '100%' AS value")).rows[0]["value"] == "100%"
    row = (
        await query(
            "SELECT $2::text AS second,$1::jsonb AS first,$2::text AS repeated,'100%' AS literal",
            [{"nested": [1, 2]}, "don't interpolate"],
        )
    ).rows[0]
    assert row == {
        "second": "don't interpolate",
        "first": {"nested": [1, 2]},
        "repeated": "don't interpolate",
        "literal": "100%",
    }


async def test_worker_stops_before_starting_another_operation(monkeypatch):
    stop = asyncio.Event()
    performed = []

    async def resume(**kwargs):
        pass

    async def queued(*args):
        return Result([{"id": "first"}, {"id": "second"}], 2)

    async def process(id):
        performed.append(id)
        stop.set()

    monkeypatch.setattr(worker, "resume_intakes", resume)
    monkeypatch.setattr(worker, "query", queued)
    monkeypatch.setattr(worker, "process_operation", process)
    await worker.tick(sync=True, stop=stop)
    assert performed == ["first"]
