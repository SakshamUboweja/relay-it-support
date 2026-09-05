"""Durable provider outbox and polling tasks; ambiguous creates only reconcile."""

from datetime import datetime, timezone
from uuid import uuid4

from .connector import ConnectorError, connector
from .db import connection, mode, query, transaction
from .policy import policy
from .workflow import enqueue, process_intake


async def save_ticket(report_id, ticket):
    await query(
        "UPDATE reports SET provider_key=$2,provider_url=$3,provider_status=$4,provider_team=$5,provider_priority=$6,synced_at=now(),state=CASE WHEN lower($4) IN ('resolved','closed','done') THEN 'resolved' ELSE 'created' END,acknowledged_at=CASE WHEN lower($4) IN ('in progress','resolved','closed','done') THEN coalesce(acknowledged_at,now()) ELSE acknowledged_at END,updated_at=now() WHERE id=$1",
        [
            report_id,
            ticket["key"],
            ticket["url"],
            ticket["status"],
            ticket["team"],
            ticket["priority"],
        ],
    )


async def fail(op, error):
    await query(
        "UPDATE connector_operations SET state='failed',last_error=$2,updated_at=now() WHERE id=$1",
        [op["id"], str(error) or "Connector failure"],
    )
    if op["kind"] == "create":
        await query(
            "UPDATE reports SET state='operator_review',updated_at=now() WHERE id=$1 AND provider_key IS NULL",
            [op["report_id"]],
        )


async def process_operation(id, provider=None):
    async with connection() as lock:
        acquired = (
            await query("SELECT pg_try_advisory_lock(hashtext($1)) locked", [str(id)], db=lock)
        ).rows[0]["locked"]
        if not acquired:
            return
        try:
            rows = (await query("SELECT * FROM connector_operations WHERE id=$1", [id])).rows
            if not rows or rows[0]["state"] not in {"pending", "unknown"}:
                return
            op = rows[0]
            report = (await query("SELECT * FROM reports WHERE id=$1", [op["report_id"]])).rows[0]
            if report["mode"] != mode():
                return
            next_at = op["next_attempt_at"]
            if isinstance(next_at, str):
                next_at = datetime.fromisoformat(next_at.replace("Z", "+00:00"))
            if next_at > datetime.now(timezone.utc):
                return
            try:
                api = provider if provider is not None else await connector()
            except Exception as error:
                await fail(op, error)
                return
            create_started = False
            create_finished = False
            try:
                payload = op["payload"]
                if op["kind"] == "create":
                    if report["provider_key"]:
                        ticket = await api.read(report["provider_key"])
                    elif op["state"] == "unknown":
                        found = await api.reconcile(payload["marker"])
                        if len(found) == 1:
                            ticket = found[0]
                        else:
                            exhausted = len(found) > 1 or op["attempts"] >= 5
                            await query(
                                "UPDATE connector_operations SET attempts=attempts+1,state=$2,last_error=$3,next_attempt_at=now()+interval '30 seconds',updated_at=now() WHERE id=$1",
                                [
                                    id,
                                    "failed" if exhausted else "unknown",
                                    "Multiple correlation matches; operator review required."
                                    if len(found) > 1
                                    else "No indexed correlation match yet. Creation will not be retried.",
                                ],
                            )
                            if exhausted:
                                await query(
                                    "UPDATE reports SET state='operator_review' WHERE id=$1 AND provider_key IS NULL",
                                    [op["report_id"]],
                                )
                            return
                    else:
                        await api.validate(payload)
                        # Commit the uncertainty BEFORE crossing the external write boundary.
                        await query(
                            "UPDATE connector_operations SET state='unknown',attempts=attempts+1,updated_at=now() WHERE id=$1",
                            [id],
                        )
                        create_started = True
                        ticket = await api.create(payload)
                        create_finished = True
                    async with transaction() as db:
                        await query(
                            "UPDATE connector_operations SET state='succeeded',external_key=$2,last_error=NULL,updated_at=now() WHERE id=$1",
                            [id, ticket["key"]],
                            db=db,
                        )
                        await query(
                            "UPDATE reports SET provider_key=$2,provider_url=$3,provider_status=$4,provider_team=$5,provider_priority=$6,synced_at=now(),state='created',updated_at=now() WHERE id=$1",
                            [
                                report["id"],
                                ticket["key"],
                                ticket["url"],
                                ticket["status"],
                                ticket["team"],
                                ticket["priority"],
                            ],
                            db=db,
                        )
                        if (
                            ticket["team"] != payload["team"]
                            or ticket["priority"] != payload["priority"]
                        ):
                            user = (
                                await query(
                                    "SELECT * FROM users WHERE id=$1", [report["owner_id"]], db=db
                                )
                            ).rows[0]
                            await enqueue(
                                db,
                                {**report, "provider_key": ticket["key"]},
                                user,
                                "update",
                                {
                                    "expected": {
                                        "team": ticket["team"],
                                        "priority": ticket["priority"],
                                    }
                                },
                            )
                else:
                    if not op["external_key"]:
                        raise ConnectorError("Update operation has no saved provider key.")
                    ticket = await api.update(
                        op["external_key"],
                        payload["team"],
                        payload["priority"],
                        payload.get("expected"),
                    )
                    await save_ticket(op["report_id"], ticket)
                    await query(
                        "UPDATE connector_operations SET state='succeeded',last_error=NULL,updated_at=now() WHERE id=$1",
                        [id],
                    )
            except Exception as error:
                definitive_rejection = (
                    isinstance(error, ConnectorError)
                    and not error.ambiguous
                    and 400 <= error.status < 500
                )
                if (
                    (isinstance(error, ConnectorError) and error.ambiguous)
                    or create_finished
                    or (create_started and not definitive_rejection)
                ):
                    # A response can be lost, or saving an accepted response can fail.
                    # Either outcome must reconcile the persisted marker, never create again.
                    await query(
                        "UPDATE connector_operations SET state='unknown',last_error=$2,next_attempt_at=now()+interval '5 seconds',updated_at=now() WHERE id=$1",
                        [id, str(error)],
                    )
                elif (
                    isinstance(error, ConnectorError)
                    and (error.retryable or error.status == 429 or error.status >= 500)
                    and op["attempts"] < 4
                ):
                    await query(
                        "UPDATE connector_operations SET state=$4,attempts=attempts+1,last_error=$2,next_attempt_at=now()+($3||' seconds')::interval WHERE id=$1",
                        [
                            id,
                            str(error),
                            max(error.retry_after, 2 ** op["attempts"]),
                            "unknown" if op["state"] == "unknown" else "pending",
                        ],
                    )
                else:
                    await fail(op, error)
        finally:
            await query("SELECT pg_advisory_unlock(hashtext($1))", [str(id)], db=lock)


async def review_timers():
    await query(
        "INSERT INTO operator_events(id,report_id,kind,detail,dedupe_key) SELECT gen_random_uuid(),id,'review-window-elapsed',jsonb_build_object('minutes',$1::int),'review:'||id FROM reports WHERE state IN ('created','submission_pending','operator_review') AND mode=$2 AND acknowledged_at IS NULL AND created_at<now()-($1||' minutes')::interval ON CONFLICT(dedupe_key) DO NOTHING",
        [policy["reviewMinutes"], mode()],
    )


async def sync_requests(provider=None, stop=None):
    api = provider if provider is not None else await connector()
    reports = (
        await query(
            "SELECT * FROM reports WHERE provider_key IS NOT NULL AND state<>'resolved' AND mode=$1 AND (synced_at IS NULL OR synced_at<now()-interval '60 seconds') LIMIT 50",
            [mode()],
        )
    ).rows
    for report in reports:
        if stop is not None and stop.is_set():
            return
        try:
            await save_ticket(report["id"], await api.read(report["provider_key"]))
        except Exception as error:
            await query(
                "INSERT INTO operator_events(id,report_id,kind,detail) VALUES($1,$2,$3,$4)",
                [
                    str(uuid4()),
                    report["id"],
                    "sync-failed",
                    {"message": str(error) or "Sync failed"},
                ],
            )


async def resume_intakes(stop=None):
    reports = (
        await query(
            "SELECT * FROM reports WHERE state='processing' AND mode=$1 ORDER BY created_at LIMIT 10",
            [mode()],
        )
    ).rows
    for report in reports:
        if stop is not None and stop.is_set():
            return
        user = (await query("SELECT * FROM users WHERE id=$1", [report["owner_id"]])).rows[0]
        await process_intake(report["id"], user)
