"""PostgreSQL-backed state machine and durable delivery regression tests."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from relay.connector import ConnectorError, DemoConnector, draft
from relay.db import query
from relay.jobs import process_operation, review_timers, sync_requests
from relay.review import approve_review, prepare_review, view_review
from relay.workflow import get_report, intake, process_intake

pytestmark = pytest.mark.asyncio

USER = {
    "id": "maya",
    "name": "Maya Chen",
    "role": "employee",
    "location": "San Francisco",
    "device": "MacBook",
    "scope": "sf",
    "external_account": None,
}
OTHER = {**USER, "id": "other", "location": "London", "scope": "london"}
OPERATOR = {**USER, "id": "operator", "role": "operator", "scope": "operators"}


@pytest_asyncio.fixture(autouse=True)
async def users(isolated_db, monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    for user in [USER, OTHER, OPERATOR]:
        await query(
            "INSERT INTO users(id,name,role,location,device,scope,external_account) VALUES($1,$2,$3,$4,$5,$6,$7)",
            list(user.values()),
        )


async def no_sources(text, user):
    return []


async def approve(report_id, user=USER):
    assert await operation(report_id) is None
    await prepare_review(report_id, user, provider=DemoConnector())
    review = await view_review(report_id, user)
    assert review["verification"]["status"] == "passed"
    assert (await get_report(report_id, user))["state"] == "awaiting_approval"
    assert await operation(report_id) is None
    await approve_review(report_id, user, review["version"], provider=DemoConnector())


async def create(text="VPN connection failure", *, sources=None, user=USER):
    id = await intake({"text": text, "submissionKey": str(uuid4())}, user)

    async def retrieve(text, user):
        return sources or []

    await process_intake(id, user, {"retrieve": retrieve})
    if (await get_report(id, user))["state"] == "review_pending":
        await approve(id, user)
    return await get_report(id, user)


async def operation(report_id, kind="create"):
    rows = (
        await query(
            "SELECT * FROM connector_operations WHERE report_id=$1 AND kind=$2 ORDER BY created_at",
            [report_id, kind],
        )
    ).rows
    return rows[0] if rows else None


async def due(id):
    await query("UPDATE connector_operations SET next_attempt_at=now() WHERE id=$1", [id])


def source(kind, service, **extra):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()),
        "kind": kind,
        "title": "Approved help",
        "body": "Reconnect Wi-Fi safely.",
        "service": service,
        "visibility": "all",
        "location": "San Francisco",
        "status": "approved",
        "metadata": {},
        "created_at": now,
        "updated_at": now,
        **extra,
    }


async def test_procedure_confirmation_resolves_without_external_ticket():
    article = source("article", "wifi", metadata={"procedure": "wifi"})
    report = await create("My Wi-Fi keeps disconnecting", sources=[article])
    assert report["state"] == "awaiting_response"
    assert report["attempted"] == []
    await intake({"reportId": report["id"], "action": "fixed", "submissionKey": str(uuid4())}, USER)
    report = await get_report(report["id"], USER)
    assert report["state"] == "resolved"
    assert report["attempted"] == [article["id"]]
    assert report["provider_key"] is None
    assert await operation(report["id"]) is None


async def test_one_clarification_then_general_intake():
    report = await create("I cannot get in")
    assert report["clarifications"] == 1
    await intake(
        {"reportId": report["id"], "text": "Still no luck", "submissionKey": str(uuid4())}, USER
    )
    await process_intake(report["id"], USER, {"retrieve": no_sources})
    report = await get_report(report["id"], USER)
    assert report["clarifications"] == 1
    assert report["state"] == "review_pending"
    assert await operation(report["id"]) is None
    assert report["decision"]["team"] == "Service Desk"
    assert report["decision"]["facts"]["impact"]["value"] is None


async def test_concurrent_submission_and_workers_create_exactly_one_ticket():
    body = {"text": "VPN broke after I changed my password", "submissionKey": str(uuid4())}
    a, b = await asyncio.gather(intake(body, USER), intake(body, USER))
    assert a == b
    await process_intake(a, USER, {"retrieve": no_sources})
    await approve(a)
    op = await operation(a)
    await asyncio.gather(process_operation(op["id"]), process_operation(op["id"]))
    report = await get_report(a, USER)
    assert report["provider_key"].startswith("DEMO-")
    assert report["provider_team"] == "Identity & Access"
    assert (await query("SELECT count(*) n FROM demo_tickets")).rows[0]["n"] == 1


async def test_lost_create_response_reconciles_without_recreating():
    report = await create()
    op = await operation(report["id"])

    class Lost(DemoConnector):
        calls = 0

        async def create(self, payload):
            self.calls += 1
            await super().create(payload)
            raise ConnectorError("Response lost", ambiguous=True)

    api = Lost()
    await process_operation(op["id"], api)
    assert (await operation(report["id"]))["state"] == "unknown"
    await due(op["id"])
    await process_operation(op["id"], api)
    assert (await operation(report["id"]))["state"] == "succeeded"
    assert api.calls == 1
    assert (await get_report(report["id"], USER))["state"] == "created"


async def test_transient_validation_retries_before_single_create():
    report = await create()
    op = await operation(report["id"])

    class Transient(DemoConnector):
        validations = 0
        creates = 0

        async def validate(self, payload):
            self.validations += 1
            if self.validations == 1:
                raise ConnectorError("Network reset", retryable=True)
            return await super().validate(payload)

        async def create(self, payload):
            self.creates += 1
            return await super().create(payload)

    api = Transient()
    await process_operation(op["id"], api)
    assert (await operation(report["id"]))["state"] == "pending"
    assert api.creates == 0
    await due(op["id"])
    await process_operation(op["id"], api)
    assert (await operation(report["id"]))["state"] == "succeeded"
    assert api.creates == 1


@pytest.mark.parametrize("failure", ["empty", "rate-limit", "multiple", "exhausted"])
async def test_unknown_creates_never_blindly_replay(failure):
    report = await create()
    op = await operation(report["id"])
    await query(
        "UPDATE connector_operations SET state='unknown',attempts=$2 WHERE id=$1",
        [op["id"], 5 if failure == "exhausted" else 1],
    )

    class Unknown(DemoConnector):
        async def create(self, payload):
            pytest.fail("Ambiguous create must never replay")

        async def reconcile(self, marker):
            if failure == "rate-limit":
                raise ConnectorError("Rate limit", retry_after=1, status=429)
            return [{}, {}] if failure == "multiple" else []

    await process_operation(op["id"], Unknown())
    assert (await operation(report["id"]))["state"] == (
        "failed" if failure in {"multiple", "exhausted"} else "unknown"
    )
    if failure in {"multiple", "exhausted"}:
        assert (await get_report(report["id"], USER))["state"] == "operator_review"


async def test_failed_routing_update_preserves_created_request():
    report = await create()
    op = await operation(report["id"])

    class WrongTeam(DemoConnector):
        async def create(self, payload):
            return await super().create({**payload, "team": "Service Desk"})

        async def update(self, *args):
            raise ConnectorError("Route failed")

    api = WrongTeam()
    await process_operation(op["id"], api)
    update = await operation(report["id"], "update")
    assert update["payload"]["expected"]["team"] == "Service Desk"
    await process_operation(update["id"], api)
    saved = await get_report(report["id"], USER)
    assert saved["state"] == "created" and saved["provider_key"]
    assert (
        await query(
            "SELECT count(*) n FROM connector_operations WHERE report_id=$1 AND kind='create'",
            [report["id"]],
        )
    ).rows[0]["n"] == 1


async def test_authorization_and_cross_mode_isolation(monkeypatch):
    body = {"text": "VPN fails", "submissionKey": str(uuid4())}
    id = await intake(body, USER)
    await process_intake(id, USER, {"retrieve": no_sources})
    with pytest.raises(ValueError, match="Not found"):
        await get_report(id, OTHER)
    assert (await get_report(id, OPERATOR))["id"] == id
    await approve(id)
    op = await operation(id)
    monkeypatch.setenv("APP_MODE", "live")

    class Guarded(DemoConnector):
        async def create(self, payload):
            pytest.fail("Live worker cannot dispatch demo operation")

    await process_operation(op["id"], Guarded())
    assert (await operation(id))["state"] == "pending"
    with pytest.raises(ValueError, match="Not found"):
        await get_report(id, OPERATOR)
    with pytest.raises(ValueError, match="another application mode"):
        await intake(body, USER)


async def test_security_support_bypass_stays_restricted():
    report = await create("I cannot get in")
    await intake(
        {
            "reportId": report["id"],
            "action": "support",
            "text": "My account was hacked",
            "submissionKey": str(uuid4()),
        },
        USER,
    )
    saved = await get_report(report["id"], USER)
    assert saved["decision"]["team"] == "Security Review"
    assert saved["state"] == "operator_review"
    assert await operation(report["id"]) is None
    assert (await get_report(report["id"], USER))["provider_key"] is None


async def test_related_follow_rechecks_visibility_and_open_status():
    incident = source(
        "incident", "atlas", status="open", visibility="sf", metadata={"curated": True}
    )
    await query(
        "INSERT INTO sources(id,kind,title,body,service,visibility,location,status,metadata) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        [
            incident[k]
            for k in [
                "id",
                "kind",
                "title",
                "body",
                "service",
                "visibility",
                "location",
                "status",
                "metadata",
            ]
        ],
    )
    report = await create("Atlas is loading slowly", sources=[incident])
    assert report["state"] == "related_suggested"
    await query("UPDATE sources SET status='closed' WHERE id=$1", [incident["id"]])
    with pytest.raises(ValueError, match="no longer available"):
        await intake(
            {"reportId": report["id"], "action": "follow", "submissionKey": str(uuid4())}, USER
        )
    await query("UPDATE sources SET status='open' WHERE id=$1", [incident["id"]])
    await intake(
        {"reportId": report["id"], "action": "follow", "submissionKey": str(uuid4())}, USER
    )
    saved = await get_report(report["id"], USER)
    assert saved["state"] == "related_reported" and saved["related_id"] == incident["id"]
    assert saved["provider_key"] is None


async def test_review_timer_deduplicates_and_respects_mode(monkeypatch):
    report = await create()
    await query(
        "UPDATE reports SET created_at=now()-interval '20 minutes' WHERE id=$1", [report["id"]]
    )
    monkeypatch.setenv("APP_MODE", "live")
    await review_timers()
    assert (
        await query("SELECT count(*) n FROM operator_events WHERE kind='review-window-elapsed'")
    ).rows[0]["n"] == 0
    monkeypatch.setenv("APP_MODE", "demo")
    await review_timers()
    await review_timers()
    assert (
        await query("SELECT count(*) n FROM operator_events WHERE kind='review-window-elapsed'")
    ).rows[0]["n"] == 1
    await query("UPDATE reports SET acknowledged_at=now() WHERE id=$1", [report["id"]])
    await review_timers()
    assert (
        await query("SELECT count(*) n FROM operator_events WHERE kind='review-window-elapsed'")
    ).rows[0]["n"] == 1


async def test_provider_resolution_and_reassignment_sync_without_rerouting():
    report = await create()
    await process_operation((await operation(report["id"]))["id"])
    report = await get_report(report["id"], USER)
    await query(
        "UPDATE demo_tickets SET team='Endpoint',status='Resolved' WHERE key=$1",
        [report["provider_key"]],
    )
    await query(
        "UPDATE reports SET synced_at=now()-interval '2 minutes' WHERE id=$1", [report["id"]]
    )
    await sync_requests(DemoConnector())
    report = await get_report(report["id"], USER)
    assert report["provider_team"] == "Endpoint" and report["state"] == "resolved"
    assert report["acknowledged_at"]


async def test_live_support_runs_extraction_preserves_evidence_and_skips_tried_procedure(
    monkeypatch,
):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    text = "My external monitor flickers. I reconnected the cables. Only I am affected. Please send this to IT support."
    article = source(
        "article", "laptop", body="Reconnect the monitor cable.", metadata={"procedure": "monitor"}
    )

    async def retrieve(text, user):
        return [article]

    async def extract(text, message_ids, procedure):
        assert procedure["id"] == article["id"]
        return {
            "usage": {"input": 30, "output": 40},
            "data": {
                "summary": "External monitor flickers after reconnecting cables",
                "service": "laptop",
                "serviceQuote": "external monitor",
                "symptomQuote": "My external monitor flickers.",
                "impactQuote": "Only I am affected.",
                "urgencyQuote": None,
                "deviceQuote": "external monitor",
                "startedQuote": None,
                "workaroundQuote": None,
                "attemptedStepsQuotes": ["I reconnected the cables."],
                "supportRequestQuote": "Please send this to IT support.",
                "procedureAttemptedQuote": "I reconnected the cables.",
                "securityQuote": None,
                "evidenceIds": message_ids,
            },
        }

    id = await intake({"text": text, "action": "support", "submissionKey": str(uuid4())}, USER)
    assert (await get_report(id, USER))["state"] == "processing"
    assert await operation(id) is None
    await process_intake(id, USER, {"retrieve": retrieve, "extract_live": extract})
    report = await get_report(id, USER)
    assert report["state"] == "review_pending"
    assert report["offered"] == []
    assert report["summary"] == "External monitor flickers after reconnecting cables"
    assert report["decision"]["team"] == "Endpoint"
    assert report["decision"]["facts"]["location"]["value"] is None
    assert report["decision"]["facts"]["attemptedSteps"]["value"] == "I reconnected the cables."
    assert report["decision"]["usage"] == {"input": 30, "output": 40}
    assert report["decision"]["reasoningEffort"] == "high"
    context = (await query("SELECT context FROM context_snapshots WHERE report_id=$1", [id])).rows[
        0
    ]["context"]
    assert context["directory"] is None and context["simulated"] is False
    assert await operation(id) is None
    candidate = draft(report, USER["external_account"])
    assert candidate["summary"] == report["summary"]
    assert "I reconnected the cables." in candidate["description"]


async def test_live_model_failure_preserves_original_message_and_restricted_security(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")

    async def unavailable(*args):
        raise RuntimeError("Provider unavailable")

    for text, team, state in [
        ("VPN cannot connect", "Service Desk", "review_pending"),
        ("My account was hacked", "Security Review", "operator_review"),
    ]:
        id = await intake({"text": text, "submissionKey": str(uuid4())}, USER)
        await process_intake(id, USER, {"retrieve": no_sources, "extract_live": unavailable})
        report = await get_report(id, USER)
        assert report["state"] == state
        assert await operation(id) is None
        assert report["decision"]["team"] == team
        assert report["decision"]["model"] == "live-failed"
        assert report["decision"]["procedure"] is None
        assert report["decision"]["facts"]["device"]["value"] is None
        assert (
            await query("SELECT body FROM messages WHERE report_id=$1 AND role='user'", [id])
        ).rows[0]["body"] == text


async def test_accepted_create_database_save_failure_reconciles_without_duplicate(monkeypatch):
    import relay.jobs as jobs

    report = await create()
    op = await operation(report["id"])
    original_query = jobs.query
    failed = False

    async def save_failure(sql, params=(), db=None):
        nonlocal failed
        if "SET state='succeeded',external_key" in sql and not failed:
            failed = True
            raise RuntimeError("Database response interrupted")
        return await original_query(sql, params, db=db)

    monkeypatch.setattr(jobs, "query", save_failure)

    class Counted(DemoConnector):
        creates = 0

        async def create(self, payload):
            self.creates += 1
            return await super().create(payload)

    api = Counted()
    await process_operation(op["id"], api)
    assert (await operation(report["id"]))["state"] == "unknown"
    assert (await get_report(report["id"], USER))["provider_key"] is None
    await due(op["id"])
    await process_operation(op["id"], api)
    assert api.creates == 1
    assert (await operation(report["id"]))["state"] == "succeeded"
    assert (await query("SELECT count(*) n FROM demo_tickets")).rows[0]["n"] == 1


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("validation", "failed"),
        ("rejected", "failed"),
        ("rate-limit", "pending"),
        ("unexpected-create", "unknown"),
    ],
)
async def test_create_error_boundary_distinguishes_safe_rejection_from_uncertainty(
    failure, expected
):
    report = await create()
    op = await operation(report["id"])

    class Failing(DemoConnector):
        async def validate(self, payload):
            if failure == "validation":
                raise RuntimeError("Bad integration configuration")
            return await super().validate(payload)

        async def create(self, payload):
            if failure == "rejected":
                raise ConnectorError("Provider rejected fields", status=400)
            if failure == "rate-limit":
                raise ConnectorError("Rate limit", status=429, retry_after=1)
            raise RuntimeError("Unexpected response handling error")

    await process_operation(op["id"], Failing())
    assert (await operation(report["id"]))["state"] == expected
