"""Start the Python web and worker together for local development."""

import asyncio
import os
import signal
import sys

from .config import ROOT, validate_environment
from .db import close_pool, migrate, mode


async def run():
    expected = sys.argv[1] if len(sys.argv) > 1 else mode()
    if expected not in {"demo", "live"} or expected != mode():
        raise ValueError(f"This command requires APP_MODE={expected} in .env")
    validate_environment()
    if not (ROOT / "out/index.html").exists():
        raise ValueError("Build the interface first: npm run build")
    if mode() == "demo":
        from .cli import seed_demo

        await migrate()
        await seed_demo()
        await close_pool()
    env = {**os.environ, "HOST": "127.0.0.1"}
    children = [
        await asyncio.create_subprocess_exec(sys.executable, "-m", module, env=env)
        for module in ["relay.web", "relay.worker"]
    ]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    waiters = [asyncio.create_task(c.wait()) for c in children]
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([*waiters, stop_task], return_when=asyncio.FIRST_COMPLETED)
        failed = any(task in done and task.result() != 0 for task in waiters)
    finally:
        for child in children:
            if child.returncode is None:
                child.terminate()
        await asyncio.gather(*waiters)
        stop_task.cancel()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run())
