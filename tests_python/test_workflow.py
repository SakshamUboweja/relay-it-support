"""PostgreSQL-backed state machine and durable delivery regression tests."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from relay import workflow
from relay.agents import build_pipeline
from relay.agents.schemas import ReviewerOutput, RoutingProposal, SingleAgentOutput
from relay.agents.tools import CasesArgs
from relay.model import Extraction
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
    decision = report["decision"]
    assert decision["pipeline"] == "deterministic" and decision["scoring"] == "v1"
    assert decision["model"] == "test-model" and decision["costUsd"] is None
    assert decision["promptVersions"] == {"intake": "relay-intake-v2"}
    assert 0 <= decision["confidence"]["value"] <= 1
    assert decision["confidence"]["band"] in {"high", "medium", "low"}
    assert decision["confidence"]["signals"]
    runs = (await query("SELECT * FROM agent_runs WHERE report_id=$1", [id])).rows
    assert len(runs) == 1
    run = runs[0]
    assert (run["status"], run["pipeline"], run["model"]) == (
        "completed",
        "deterministic",
        "test-model",
    )
    assert run["id"] == decision["agentRunId"]
    assert run["usage"] == {"input": 30, "output": 40, "cached": 0, "reasoning": 0}
    linked = (await query("SELECT decision FROM decisions WHERE id=$1", [run["decision_id"]])).rows
    assert linked[0]["decision"]["pipeline"] == "deterministic"
    steps = (
        await query(
            "SELECT kind,status,prompt_version,usage,output_summary FROM agent_steps WHERE run_id=$1 ORDER BY seq",
            [run["id"]],
        )
    ).rows
    assert [(s["kind"], s["status"]) for s in steps] == [("model_call", "ok"), ("policy", "ok")]
    assert steps[0]["prompt_version"] == "relay-intake-v2"
    assert steps[0]["usage"]["input"] == 30
    assert "supportRequestQuote" in steps[0]["output_summary"]
    assert "Endpoint" in steps[1]["output_summary"]


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
        assert "model-unavailable" in report["decision"]["reasons"]
        assert report["decision"]["pipeline"] == "deterministic"
        assert (
            await query("SELECT body FROM messages WHERE report_id=$1 AND role='user'", [id])
        ).rows[0]["body"] == text
        assert (
            (
                await query(
                    "SELECT body FROM messages WHERE report_id=$1 AND role='assistant' ORDER BY created_at DESC LIMIT 1",
                    [id],
                )
            )
            .rows[0]["body"]
            .startswith("Live model failed.")
        )
        run = (await query("SELECT * FROM agent_runs WHERE report_id=$1", [id])).rows[0]
        assert run["status"] == "failed"
        steps = (
            await query(
                "SELECT kind,status,error FROM agent_steps WHERE run_id=$1 ORDER BY seq",
                [run["id"]],
            )
        ).rows
        assert [(s["kind"], s["status"]) for s in steps] == [
            ("model_call", "error"),
            ("policy", "ok"),
        ]
        assert "Provider unavailable" in steps[0]["error"]


async def test_demo_intake_persists_a_skipped_run_with_a_policy_step():
    report = await create("VPN connection failure")
    decision = report["decision"]
    assert decision["pipeline"] == "deterministic"
    assert decision["model"] == "deterministic-demo-v1"
    assert decision["usage"] == {"input": 0, "output": 0}
    assert decision["costUsd"] == 0.0 and decision["promptVersions"] == {}
    assert decision["confidence"]["band"] in {"high", "medium", "low"}
    assert decision["confidence"]["signals"][0]["kind"] == "deterministicMargin"
    runs = (await query("SELECT * FROM agent_runs WHERE report_id=$1", [report["id"]])).rows
    assert len(runs) == 1
    assert (runs[0]["status"], runs[0]["pipeline"]) == ("skipped", "deterministic")
    assert runs[0]["id"] == decision["agentRunId"]
    steps = (
        await query("SELECT kind,role,status FROM agent_steps WHERE run_id=$1", [runs[0]["id"]])
    ).rows
    assert steps == [{"kind": "policy", "role": "policy", "status": "ok"}]


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


async def test_single_pipeline_run_records_the_arm_and_its_routing_proposal(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    text = "My managed laptop cannot join the office Wi-Fi; it says unable to connect."

    async def parse(**kwargs):
        sent = json.loads(kwargs["input"][1]["content"][0]["text"])
        return SimpleNamespace(
            status="completed",
            usage=SimpleNamespace(input_tokens=30, output_tokens=40),
            output_parsed=SingleAgentOutput(
                summary="Managed laptop cannot join the office Wi-Fi",
                service="wifi",
                serviceQuote="office Wi-Fi",
                symptomQuote="cannot join the office Wi-Fi",
                impactQuote=None,
                urgencyQuote=None,
                deviceQuote="managed laptop",
                startedQuote=None,
                workaroundQuote=None,
                attemptedStepsQuotes=[],
                supportRequestQuote=None,
                procedureAttemptedQuote=None,
                securityQuote=None,
                evidenceIds=sent["evidenceIds"],
                team="Network",
                abstain=False,
                blockedQuote=None,
                broadImpactQuote=None,
                rationale="The message names the office Wi-Fi.",
                probability=0.9,
                citedSourceIds=[],
            ),
        )

    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    runtime = workflow.ModelRuntime
    monkeypatch.setattr(
        workflow,
        "ModelRuntime",
        lambda *args, **kwargs: runtime(*args, **kwargs, client_factory=lambda: client),
    )
    id = await intake({"text": text, "submissionKey": str(uuid4())}, USER)
    await process_intake(
        id,
        USER,
        {"retrieve": no_sources, "pipeline": build_pipeline("single", extract=None)},
    )
    decision = (await get_report(id, USER))["decision"]
    assert decision["pipeline"] == "single"
    assert (decision["team"], decision["service"]) == ("Network", "wifi")
    assert "model-tie-break" in decision["reasons"]
    assert decision["proposal"] == {
        "team": "Network",
        "service": "wifi",
        "abstain": False,
        "probability": 0.9,
        "rationale": "The message names the office Wi-Fi.",
        "citedSourceIds": [],
    }
    assert decision["promptVersions"]["single"] == "relay-single-v1"
    assert decision["promptVersion"] == "relay-single-v1"
    assert decision["confidence"]["agentRationale"] == "The message names the office Wi-Fi."
    run = (await query("SELECT * FROM agent_runs WHERE report_id=$1", [id])).rows[0]
    assert (run["pipeline"], run["status"], run["model"]) == ("single", "completed", "test-model")
    assert run["id"] == decision["agentRunId"]
    steps = (
        await query(
            "SELECT role,kind,prompt_version FROM agent_steps WHERE run_id=$1 ORDER BY seq",
            [run["id"]],
        )
    ).rows
    assert [(s["role"], s["kind"]) for s in steps] == [
        ("intake", "model_call"),
        ("policy", "policy"),
    ]
    assert steps[0]["prompt_version"] == "relay-single-v1"


async def test_multi_pipeline_run_records_every_role_and_the_review(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    from relay.agents.prompts import REVIEWER_PROMPT, TRIAGE_PROMPT

    text = "My managed laptop cannot join the office Wi-Fi; it says unable to connect."
    reviewed = source("case", "wifi", title="Office Wi-Fi join failure", metadata={"reviewed": True, "team": "Network"})

    class FunctionCall(SimpleNamespace):
        def model_dump(self, *, exclude_none=False, exclude=()):
            return {k: v for k, v in vars(self).items() if k not in exclude and v is not None}

    triage_calls = []

    async def parse(**kwargs):
        system = kwargs["input"][0]["content"]
        sent = json.loads(kwargs["input"][1]["content"][0]["text"])
        response = SimpleNamespace(
            status="completed",
            usage=SimpleNamespace(input_tokens=30, output_tokens=40),
            output=[],
            output_parsed=None,
        )
        if system == TRIAGE_PROMPT:
            triage_calls.append(kwargs)
            if len(triage_calls) == 1:
                response.output = [
                    FunctionCall(
                        type="function_call",
                        id="fc_1",
                        name="similar_cases",
                        call_id="call_1",
                        arguments=json.dumps({"service": "wifi"}),
                        parsed_arguments=CasesArgs(service="wifi"),
                        status="completed",
                    )
                ]
                return response
            response.output_parsed = RoutingProposal(
                service="wifi",
                team="Network",
                abstain=False,
                blockedQuote=None,
                broadImpactQuote=None,
                securityQuote=None,
                rationale="A reviewed case with the same symptom went to Network.",
                probability=0.85,
                citedSourceIds=[reviewed["id"]],
            )
        elif system == REVIEWER_PROMPT:
            assert sent["citedSources"] == [
                {"id": reviewed["id"], "title": "Office Wi-Fi join failure", "team": "Network"}
            ]
            response.output_parsed = ReviewerOutput(
                verdict="accept", agreementProbability=0.9, issues=[], securityQuote=None
            )
        else:
            response.output_parsed = Extraction(
                summary="Managed laptop cannot join the office Wi-Fi",
                service="wifi",
                serviceQuote="office Wi-Fi",
                symptomQuote="cannot join the office Wi-Fi",
                impactQuote=None,
                urgencyQuote=None,
                deviceQuote="managed laptop",
                startedQuote=None,
                workaroundQuote=None,
                attemptedStepsQuotes=[],
                supportRequestQuote=None,
                procedureAttemptedQuote=None,
                securityQuote=None,
                evidenceIds=sent["evidenceIds"],
            )
        return response

    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    runtime = workflow.ModelRuntime
    monkeypatch.setattr(
        workflow,
        "ModelRuntime",
        lambda *args, **kwargs: runtime(*args, **kwargs, client_factory=lambda: client),
    )

    async def retrieve(text, user):
        return [reviewed]

    id = await intake({"text": text, "submissionKey": str(uuid4())}, USER)
    await process_intake(
        id, USER, {"retrieve": retrieve, "pipeline": build_pipeline("multi", extract=None)}
    )
    report = await get_report(id, USER)
    decision = report["decision"]
    assert report["state"] == "review_pending"
    assert decision["pipeline"] == "multi"
    assert (decision["team"], decision["service"]) == ("Network", "wifi")
    assert "model-tie-break" in decision["reasons"]
    assert decision["proposal"]["citedSourceIds"] == [reviewed["id"]]
    assert decision["reviewer"] == {"verdict": "accept", "agreementProbability": 0.9, "issues": []}
    assert decision["promptVersions"] == {
        "intake": "relay-intake-v2",
        "triage": "relay-triage-v1",
        "reviewer": "relay-reviewer-v1",
    }
    assert decision["promptVersion"] == "relay-intake-v2"
    assert decision["usage"] == {"input": 120, "output": 160}
    assert {s["kind"] for s in decision["confidence"]["signals"]} >= {"agentProbability", "agreement"}
    run = (await query("SELECT * FROM agent_runs WHERE report_id=$1", [id])).rows[0]
    assert (run["pipeline"], run["status"], run["model"]) == ("multi", "completed", "test-model")
    assert run["id"] == decision["agentRunId"]
    assert run["outcome"]["verdict"] == "accept"
    steps = (
        await query(
            "SELECT role,kind,status,tool_name,tool_args,tool_result_summary,prompt_version FROM agent_steps WHERE run_id=$1 ORDER BY seq",
            [run["id"]],
        )
    ).rows
    assert [(s["role"], s["kind"]) for s in steps] == [
        ("intake", "model_call"),
        ("triage", "model_call"),
        ("triage", "tool_call"),
        ("triage", "model_call"),
        ("reviewer", "model_call"),
        ("policy", "policy"),
    ]
    assert all(s["status"] == "ok" for s in steps)
    tool = steps[2]
    assert (tool["tool_name"], tool["tool_args"]) == ("similar_cases", {"service": "wifi"})
    assert "Never sent" not in tool["tool_result_summary"]
    assert reviewed["id"] in tool["tool_result_summary"]
    assert [s["prompt_version"] for s in steps[:5]] == [
        "relay-intake-v2",
        "relay-triage-v1",
        None,
        "relay-triage-v1",
        "relay-reviewer-v1",
    ]
