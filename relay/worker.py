"""Poll the durable outbox; per-operation PostgreSQL locks prevent duplicate workers."""

import asyncio
import logging
import os
import signal
import time

from .config import validate_environment
from .db import close_pool, mode, query
from .jobs import process_operation, resume_intakes, review_timers, sync_requests
from .attachments import process_attachments, expire_files


async def tick(sync=False, stop=None):
    if stop is not None and stop.is_set():
        return
    await resume_intakes(stop=stop)
    if stop is not None and stop.is_set():
        return
    operations = (
        await query(
            "SELECT o.id FROM connector_operations o JOIN reports r ON r.id=o.report_id WHERE o.state IN ('pending','unknown') AND o.next_attempt_at<=now() AND r.mode=$1 ORDER BY o.next_attempt_at LIMIT 50",
            [mode()],
        )
    ).rows
    for operation in operations:
        if stop is not None and stop.is_set():
            return
        await process_operation(operation["id"])
    if stop is not None and stop.is_set():
        return
    await review_timers()
    await process_attachments(stop=stop)
    if sync:
        await expire_files()
        await sync_requests(stop=stop)


async def run():
    validate_environment(cloud=bool(os.getenv("RAILWAY_ENVIRONMENT_ID")))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def heartbeat():
        while not stop.is_set():
            try:
                await query(
                    "INSERT INTO worker_health VALUES('main',now()) ON CONFLICT(id) DO UPDATE SET last_seen=now()"
                )
            except Exception as exc:
                logging.error("Worker heartbeat failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass

    pulse = asyncio.create_task(heartbeat())
    last_sync = 0.0
    print(
        "Relay Python worker running: durable outbox, intake recovery, status sync, review timer.",
        flush=True,
    )
    try:
        while not stop.is_set():
            try:
                sync = time.monotonic() - last_sync > 60
                await tick(sync=sync, stop=stop)
                if sync:
                    last_sync = time.monotonic()
            except Exception as exc:
                logging.error("Worker tick failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
    finally:
        stop.set()
        await pulse
        await close_pool()


if __name__ == "__main__":
    asyncio.run(run())
