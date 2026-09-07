"""Operator commands. Secrets are read from environment, never committed."""

import argparse
import asyncio
import base64
import json
import os
import struct
import zlib
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .config import validate_environment
from .db import close_pool, migrate, mode, query, transaction
from .fixtures import articles, catalog, users


def probe_png(size=8):
    """A solid grey RGB PNG built in code, so the vision preflight needs no fixture file."""

    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\x80\x80\x80" * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


async def seed_demo():
    if mode() != "demo":
        raise ValueError("Synthetic demo seeding requires APP_MODE=demo")
    for user in users:
        await query(
            "INSERT INTO users(id,name,role,location,device,scope) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
            [user[k] for k in ("id", "name", "role", "location", "device", "scope")],
        )
    for i, article in enumerate(articles):
        await query(
            "INSERT INTO sources(id,kind,title,body,service,metadata,created_at,updated_at) VALUES($1,'article',$2,$3,$4,$5,now()-interval '30 days',now()) ON CONFLICT(id) DO NOTHING",
            [
                f"kb-{i + 1}",
                article[1],
                article[2],
                article[0],
                {"synthetic": True, "procedure": article[3]},
            ],
        )
    for i in range(100):
        service = catalog[i % 5]
        auth = service["id"] == "vpn" and i % 2 == 0
        symptom = [
            "connection failure",
            "unavailable",
            "intermittent failure",
            "error",
            "not responding",
        ][(i // 5) % 5]
        title = (
            "VPN authentication rejected after password change"
            if auth
            else f"{service['name']} {symptom}"
        )
        await query(
            "INSERT INTO sources(id,kind,title,body,service,metadata,created_at,updated_at) VALUES($1,'case',$2,$3,$4,$5,now()-interval '60 days',now()-interval '31 days') ON CONFLICT(id) DO NOTHING",
            [
                f"case-{i + 1}",
                title,
                f"{title}. Sanitized synthetic historical case. No inference about this employee's completed steps.",
                service["id"],
                {
                    "synthetic": True,
                    "reviewed": True,
                    "team": "Identity & Access" if auth else service["team"],
                },
            ],
        )
    for id, status, scope, location, title, age in [
        ("inc-atlas-sf", "open", "sf", "San Francisco", "Atlas is responding slowly", 0),
        ("inc-atlas-london", "closed", "london", "London", "Atlas dashboard disruption", 48),
        (
            "inc-vpn-restricted",
            "open",
            "operators",
            "San Francisco",
            "Private VPN investigation",
            0,
        ),
    ]:
        await query(
            "INSERT INTO sources(id,kind,title,body,service,visibility,location,status,metadata,created_at,updated_at) VALUES($1,'incident',$2,$3,$4,$5,$6,$7,$8,now()-($9||' hours')::interval,now()-($9||' hours')::interval) ON CONFLICT DO NOTHING",
            [
                id,
                title,
                "Approved shared summary. Engineers are investigating intermittent loading failures. No individual reports are disclosed.",
                "atlas" if "atlas" in id else "vpn",
                scope,
                location,
                status,
                {"synthetic": True, "curated": True},
                age,
            ],
        )


async def copy_sources():
    source_url, target_url = os.environ["SOURCE_DATABASE_URL"], os.environ["DATABASE_URL"]
    if source_url == target_url:
        raise ValueError("Source and target must be separate databases.")
    async with await psycopg.AsyncConnection.connect(source_url, row_factory=dict_row) as source:
        rows = await (
            await source.execute(
                "SELECT * FROM sources WHERE metadata->>'synthetic'='true' AND kind IN ('article','case') AND visibility='all' AND status='approved'"
            )
        ).fetchall()
    copied = 0
    async with transaction() as db:
        for s in rows:
            result = await query(
                "INSERT INTO sources(id,kind,title,body,service,visibility,location,status,metadata,embedding,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) ON CONFLICT(id) DO NOTHING",
                [
                    s[k]
                    for k in (
                        "id",
                        "kind",
                        "title",
                        "body",
                        "service",
                        "visibility",
                        "location",
                        "status",
                        "metadata",
                        "embedding",
                        "created_at",
                        "updated_at",
                    )
                ],
                db=db,
            )
            copied += result.rowcount
    print(json.dumps({"eligibleSyntheticSources": len(rows), "inserted": copied}))


async def run(args):
    if args.command in {"setup", "deploy-migrate"}:
        if args.command == "deploy-migrate":
            validate_environment(cloud=True)
        await migrate()
        if mode() == "demo":
            await seed_demo()
            print("Database ready: 3 profiles, 20 articles, 100 cases, 3 incident fixtures.")
        else:
            print("Live schema migrated. No demo identities or data installed.")
    elif args.command == "session":
        from .auth import issue_session

        if mode() != "live":
            raise ValueError("Session issuance requires live mode")
        print("Paste this eight-hour session token into Relay. Treat it as a credential.")
        print(await issue_session(args.user_id))
    elif args.command == "provision-user":
        if mode() != "live":
            raise ValueError("User provisioning requires APP_MODE=live")
        if not 1 <= len(args.user_id) <= 100 or not 1 <= len(args.name) <= 200:
            raise ValueError("User ID or name length is invalid")
        result = await query(
            "INSERT INTO users(id,name,role,location,device,scope) VALUES($1,$2,$3,'Unknown','Unknown',$4) ON CONFLICT(id) DO NOTHING RETURNING id",
            [
                args.user_id,
                args.name,
                args.role,
                "operators" if args.role == "operator" else "employees",
            ],
        )
        print(
            "User provisioned."
            if result.rowcount
            else "User already exists; profile and permissions unchanged."
        )
    elif args.command == "copy-sandbox-sources":
        await copy_sources()
    elif args.command == "embed-sources":
        from .model import embed

        if mode() != "live":
            raise ValueError("Embedding indexing requires live mode")
        for source in (
            await query(
                "SELECT id,title,body FROM sources WHERE kind IN ('article','case') AND status='approved' AND embedding IS NULL"
            )
        ).rows:
            await query(
                "UPDATE sources SET embedding=$2::vector WHERE id=$1",
                [source["id"], json.dumps(await embed(source["title"] + "\n" + source["body"]))],
            )
            print("Indexed " + source["id"])
    elif args.command == "preflight":
        from .connector import connector
        from .model import embed, extract_live, model_settings

        await query("SELECT 1")
        print(json.dumps({"mode": mode(), "jira": await (await connector()).discover()}))
        if mode() == "live":
            result = await extract_live("Corporate VPN cannot connect.", ["preflight-message"])
            vector = await embed("VPN connectivity")
            print(
                json.dumps(
                    {
                        "model": os.environ["OPENAI_MODEL"],
                        "settings": model_settings(),
                        "structuredOutput": bool(result["data"]),
                        "embeddingDimensions": len(vector),
                        "usage": result["usage"],
                    }
                )
            )
            image = {
                "data_url": "data:image/png;base64," + base64.b64encode(probe_png()).decode(),
                "detail": os.getenv("RELAY_IMAGE_DETAIL", "auto"),
            }
            try:
                seen = await extract_live(
                    "The attached screenshot shows what I see.", ["preflight-message"], image=image
                )
                vision = {"vision": bool(seen["data"]["imageObservations"])}
            except Exception as error:
                vision = {"vision": False, "error": str(error) or type(error).__name__}
            print(json.dumps(vision))
    elif args.command == "retention":
        from .policy import policy

        rows = (
            await query(
                "SELECT id FROM reports WHERE state='resolved' AND mode=$2 AND updated_at<now()-($1||' days')::interval",
                [policy["retentionDays"], mode()],
            )
        ).rows
        print(f"{len(rows)} resolved reports qualify for retention deletion.")
        if args.apply:
            async with transaction() as db:
                for row in rows:
                    await query("DELETE FROM reports WHERE id=$1", [row["id"]], db=db)
            print("Applied retention; provider requests unchanged.")
        else:
            print("Dry run. Use --apply after reviewing the retention policy.")
    elif args.command == "eval":
        if args.arm == "rules-v1":
            harness_only = args.fit_calibration or args.ids or args.limit is not None
            if harness_only or args.max_usd is not None or args.no_write or args.split != "all":
                raise ValueError(
                    "--arm rules-v1 runs the legacy evaluation only; use --arm all"
                    " (or rules-v2, single, multi) with the harness flags."
                )
            from .evaluate import evaluate

            await evaluate()
        else:
            from .evaluate_arms import EvalOptions, evaluate_arms

            await evaluate_arms(
                EvalOptions(
                    arm=args.arm,
                    split=args.split,
                    effort=args.effort,
                    limit=args.limit,
                    ids=args.ids,
                    resume=args.resume,
                    concurrency=args.concurrency,
                    max_usd=args.max_usd,
                    fit_calibration=args.fit_calibration,
                    write=not args.no_write,
                )
            )
    elif args.command == "smoke-live":
        from .connector import connector

        if mode() != "live" or os.getenv("ALLOW_LIVE_SMOKE") != "1":
            raise ValueError("Explicit sandbox opt-in required: ALLOW_LIVE_SMOKE=1, APP_MODE=live")
        provider = await connector()
        draft = dict(
            summary="[Relay sandbox smoke] synthetic VPN incident",
            description="Synthetic test. Impact and urgency unknown. No real employee data.",
            team="Network",
            priority="normal",
            restricted=False,
            externalAccount=None,
            marker="relay" + uuid4().hex,
        )
        await provider.validate(draft)
        print("Correlation marker:", draft["marker"])
        created = await provider.create(draft)
        confirmed = await provider.update(
            created["key"],
            draft["team"],
            draft["priority"],
            {"team": created["team"], "priority": created["priority"]},
        )
        print(json.dumps({"status": "verified", "ticket": confirmed}))


def _ids(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in [
        "setup",
        "deploy-migrate",
        "copy-sandbox-sources",
        "embed-sources",
        "preflight",
        "smoke-live",
    ]:
        commands.add_parser(name)
    session = commands.add_parser("session")
    session.add_argument("user_id")
    user = commands.add_parser("provision-user")
    user.add_argument("user_id")
    user.add_argument("name")
    user.add_argument("role", choices=["employee", "operator"], default="employee", nargs="?")
    commands.add_parser("retention").add_argument("--apply", action="store_true")
    # `eval` alone is the legacy deterministic run; any other arm compares arms with cached
    # live calls. Calibration is only ever fitted on the dev split.
    evaluation = commands.add_parser("eval")
    evaluation.add_argument(
        "--arm", choices=["rules-v1", "rules-v2", "single", "multi", "all"], default="rules-v1"
    )
    evaluation.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    evaluation.add_argument(
        "--effort", choices=["none", "low", "medium", "high", "xhigh", "max"], default="medium"
    )
    evaluation.add_argument("--limit", type=int)
    evaluation.add_argument("--ids", type=_ids)
    evaluation.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    evaluation.add_argument("--concurrency", type=int, default=4)
    evaluation.add_argument("--max-usd", type=float)
    evaluation.add_argument("--fit-calibration", action="store_true")
    evaluation.add_argument("--no-write", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()

    async def execute():
        try:
            await run(args)
        finally:
            await close_pool()

    asyncio.run(execute())


if __name__ == "__main__":
    main()
