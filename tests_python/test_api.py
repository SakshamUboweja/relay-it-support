import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest

from relay import api, review, workflow
from relay.agents import Pipeline
from relay.agents.schemas import PipelineResult
from relay.api import app
from relay.auth import issue_session
from relay.cli import seed_demo
from relay.connector import DemoConnector
from relay.db import query
from relay.jobs import process_operation, resume_intakes

pytestmark = pytest.mark.usefixtures("isolated_db")


@pytest.fixture
async def client():
    await seed_demo()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:3000",
        headers={"Origin": "http://127.0.0.1:3000"},
    ) as client:
        yield client


async def test_demo_http_contract_and_authorization(client):
    assert (await client.get("/api/health")).json() == {"status": "ok"}
    boot = (await client.get("/api/bootstrap")).json()
    assert boot["user"]["id"] == "maya"
    assert len(boot["profiles"]) == 3
    assert [i["id"] for i in boot["incidents"]] == ["inc-atlas-sf"]
    body = {"text": "VPN broke after I changed my password", "submissionKey": str(uuid4())}
    created = await client.post("/api/intake", json=body)
    assert created.status_code == 200
    id = created.json()["id"]
    replay = await client.post("/api/intake", json=body)
    assert replay.json() == {"id": id}
    operations = (await query("SELECT id FROM connector_operations WHERE report_id=$1", [id])).rows
    assert operations == []
    preview = await client.get("/api/review", params={"id": id})
    assert preview.status_code == 200
    review = preview.json()["review"]
    assert review["verification"]["status"] == "passed"
    assert review["approvedAt"] is None
    assert review["form"]["values"]["summary"]
    assert review["form"]["values"]["description"]
    assert review["pipeline"] == "single"
    assert 0 <= review["confidence"]["value"] <= 1 and review["confidence"]["why"]
    assert (await client.get("/api/reports", params={"id": id})).json()["report"][
        "provider_key"
    ] is None
    prior_version = review["version"]
    verified = await client.post(
        "/api/review",
        json={"reportId": id, "version": prior_version, "values": review["form"]["values"]},
    )
    assert verified.status_code == 200
    review = verified.json()["review"]
    assert review["version"] == prior_version + 1
    assert review["verification"]["status"] == "passed"
    assert (await query("SELECT id FROM connector_operations WHERE report_id=$1", [id])).rows == []
    assert (
        await client.post("/api/review/approve", json={"reportId": id, "version": prior_version})
    ).status_code == 409
    assert (
        await client.post(
            "/api/review/approve",
            json={"reportId": id, "version": review["version"]},
            headers={"origin": "https://evil.test"},
        )
    ).status_code == 403
    approved = await client.post(
        "/api/review/approve", json={"reportId": id, "version": review["version"]}
    )
    assert approved.status_code == 200
    replay_approval = await client.post(
        "/api/review/approve", json={"reportId": id, "version": review["version"]}
    )
    assert replay_approval.status_code == 200
    operations = (
        await query(
            "SELECT id FROM connector_operations WHERE report_id=$1 AND kind='create'", [id]
        )
    ).rows
    assert len(operations) == 1
    for operation in operations:
        await process_operation(operation["id"])
    detail = (await client.get("/api/reports", params={"id": id})).json()
    assert detail["report"]["provider_key"].startswith("DEMO-")
    assert detail["report"]["provider_team"] == "Identity & Access"
    assert detail["operations"] == []
    assert [r["status"] for r in detail["trace"]["runs"]] == ["skipped"]
    run = detail["trace"]["runs"][0]
    assert run["pipeline"] == "single" and run["createdAt"]
    assert not {"outcome", "budget", "pricingVersion"} & set(run)
    assert [s["kind"] for s in run["steps"]] == ["policy"]
    assert not {"toolArgs", "toolResultSummary", "error", "detail"} & set(run["steps"][0])
    assert (await client.get("/api/operations")).status_code == 403
    assert (await client.get("/api/reports?all=1")).status_code == 403
    assert (await client.post("/api/session", json={"userId": "jordan"})).status_code == 200
    assert (await client.get("/api/reports", params={"id": id})).status_code == 404
    assert (await client.get("/api/review", params={"id": id})).status_code == 404
    assert (
        await client.post(
            "/api/review/approve", json={"reportId": id, "version": review["version"]}
        )
    ).status_code == 404
    assert (await client.get("/api/bootstrap")).json()["incidents"] == []
    await client.post("/api/session", json={"userId": "alex"})
    assert (await client.get("/api/reports", params={"id": id})).status_code == 200
    assert (await client.get("/api/review", params={"id": id})).status_code == 404
    operator_trace = (await client.get("/api/reports", params={"id": id})).json()["trace"]
    assert "outcome" in operator_trace["runs"][0]
    assert "detail" in operator_trace["runs"][0]["steps"][0]
    versions = (await client.get("/api/operations")).json()["versions"]
    assert versions["calibration"] in (True, False)
    assert {k: v for k, v in versions.items() if k != "calibration"} == {
        "policy": "northstar-1.0",
        "scoring": "v2",
        "prompt": "relay-intake-v3",
        "verifier": "ticket-verifier-v1",
        "pipeline": "single",
        "pricing": "2026-09-06-openrouter",
    }
    correction = await client.post(
        "/api/operations",
        json={
            "reportId": id,
            "action": "correct",
            "team": "Network",
            "priority": "normal",
            "reason": "Synthetic operator correction",
        },
    )
    assert correction.status_code == 200
    for op in (
        await query(
            "SELECT id FROM connector_operations WHERE report_id=$1 AND kind='update'", [id]
        )
    ).rows:
        await process_operation(op["id"])
    assert (await client.get("/api/reports", params={"id": id})).json()["report"][
        "provider_team"
    ] == "Network"
    assert (
        await client.post("/api/operations", json={"reportId": id, "action": "acknowledge"})
    ).status_code == 200
    assert (await client.get("/api/reports", params={"id": id})).json()["report"]["acknowledged_at"]


async def test_live_session_cookie_compatibility_expiry_and_origin(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("APP_ORIGIN", "https://relay.test")
    client.base_url = httpx.URL("https://relay.test")
    client.headers["origin"] = "https://relay.test"
    assert (await client.get("/api/bootstrap")).status_code == 401
    # This is the original Node token format: SHA-256 hex in sessions, raw hex cookie.
    legacy = "1" * 64
    await query(
        "INSERT INTO sessions VALUES($1,'maya',now()+interval '1 hour')",
        [hashlib.sha256(legacy.encode()).hexdigest()],
    )
    login = await client.post("/api/session", json={"token": legacy})
    assert login.status_code == 200
    assert all(
        value in login.headers["set-cookie"].lower()
        for value in ["httponly", "secure", "samesite=strict"]
    )
    assert (await client.get("/api/bootstrap")).json()["profiles"] == []
    token = await issue_session("maya")
    assert len(token) == 64
    await query(
        "UPDATE sessions SET expires_at=now()-interval '1 second' WHERE token_hash=$1",
        [hashlib.sha256(legacy.encode()).hexdigest()],
    )
    assert (await client.get("/api/bootstrap")).status_code == 401
    assert (
        await client.post(
            "/api/session", json={"token": token}, headers={"origin": "https://evil.test"}
        )
    ).status_code == 403


async def test_demo_hmac_cookie_compatibility(client):
    secret = "test-session-secret-with-at-least-32-characters"
    raw = "alex." + hmac.new(secret.encode(), b"alex", hashlib.sha256).hexdigest()
    client.cookies.set("relay_session", raw)
    assert (await client.get("/api/bootstrap")).json()["user"]["id"] == "alex"
    client.cookies.set("relay_session", "alex.invalid")
    assert (await client.get("/api/bootstrap")).status_code == 401


async def test_report_listing_and_operations_are_mode_scoped(client):
    body = {"text": "VPN broke after I changed my password", "submissionKey": str(uuid4())}
    report_id = (await client.post("/api/intake", json=body)).json()["id"]
    await query("UPDATE reports SET mode='live' WHERE id=$1", [report_id])
    assert (await client.get("/api/reports")).json()["reports"] == []
    await client.post("/api/session", json={"userId": "alex"})
    assert (await client.get("/api/reports?all=1")).json()["reports"] == []
    assert (await client.get("/api/operations")).json()["counts"] == []


async def test_invalid_input_and_restricted_correction(client):
    assert (
        await client.post("/api/intake", json={"text": "hi", "submissionKey": "bad"})
    ).status_code == 400
    assert (await client.post("/api/intake", content="x" * 15001)).status_code == 400
    assert (await client.get("/api/reports?id=bad")).status_code == 400
    data = {
        "text": "I opened a phishing link and entered my password",
        "submissionKey": str(uuid4()),
        "action": "support",
    }
    id = (await client.post("/api/intake", json=data)).json()["id"]
    assert (await query("SELECT id FROM connector_operations WHERE report_id=$1", [id])).rows == []
    await client.post("/api/session", json={"userId": "alex"})
    result = await client.post(
        "/api/operations",
        json={
            "reportId": id,
            "action": "correct",
            "team": "Security Review",
            "priority": "urgent",
            "reason": "synthetic",
        },
    )
    assert result.status_code == 400
    assert (await client.get("/api/reports", params={"id": id})).json()["report"][
        "provider_key"
    ] is None


async def test_live_multi_intake_returns_processing_and_the_worker_completes_it(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("APP_ORIGIN", "https://relay.test")
    monkeypatch.setenv("RELAY_PIPELINE", "multi")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.delenv("RELAY_INTAKE_INLINE", raising=False)
    client.base_url = httpx.URL("https://relay.test")
    client.headers["origin"] = "https://relay.test"
    client.cookies.set("relay_session", await issue_session("maya"))
    body = {"text": "VPN broke after I changed my password", "submissionKey": str(uuid4())}
    created = await client.post("/api/intake", json=body)
    assert created.status_code == 200
    id = created.json()["id"]
    assert created.json() == {"id": id, "state": "processing"}
    report = (await query("SELECT state FROM reports WHERE id=$1", [id])).rows[0]
    assert report["state"] == "processing"
    assert (await query("SELECT 1 FROM ticket_reviews WHERE report_id=$1", [id])).rows == []
    runs = (await query("SELECT 1 FROM agent_runs WHERE report_id=$1", [id])).rows
    assert runs == []

    async def no_sources(text, user):
        return []

    async def run(ctx, rt):
        extraction = {
            "summary": "VPN fails after a password change",
            "service": "vpn",
            "serviceQuote": "VPN",
            "symptomQuote": "VPN broke",
            "impactQuote": None,
            "urgencyQuote": None,
            "deviceQuote": None,
            "startedQuote": None,
            "workaroundQuote": None,
            "attemptedStepsQuotes": [],
            "supportRequestQuote": None,
            "procedureAttemptedQuote": None,
            "securityQuote": None,
            "evidenceIds": ctx.message_ids,
        }
        finished = rt.finish(
            pipeline=ctx.pipeline, scoring="v1", status="completed", outcome={"extraction": "validated"}
        )
        return PipelineResult(run=finished, extraction=extraction, summary=extraction["summary"])

    monkeypatch.setattr(workflow, "retrieve", no_sources)
    monkeypatch.setattr(workflow, "build_pipeline", lambda name, extract: Pipeline(name, run))

    async def demo_connector():
        return DemoConnector()

    monkeypatch.setattr(review, "connector", demo_connector)
    await resume_intakes()
    report = (await query("SELECT state,summary,decision FROM reports WHERE id=$1", [id])).rows[0]
    assert report["state"] == "awaiting_approval"
    assert report["summary"] == "VPN fails after a password change"
    assert report["decision"]["pipeline"] == "multi"
    assert report["decision"]["team"] == "Identity & Access"
    assert (await query("SELECT 1 FROM ticket_reviews WHERE report_id=$1", [id])).rows
    run_row = (await query("SELECT pipeline,status FROM agent_runs WHERE report_id=$1", [id])).rows
    assert run_row == [{"pipeline": "multi", "status": "completed"}]


async def test_inline_override_keeps_the_multi_arm_in_the_request(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("APP_ORIGIN", "https://relay.test")
    monkeypatch.setenv("RELAY_PIPELINE", "multi")
    monkeypatch.setenv("RELAY_INTAKE_INLINE", "1")
    client.base_url = httpx.URL("https://relay.test")
    client.headers["origin"] = "https://relay.test"
    client.cookies.set("relay_session", await issue_session("maya"))
    seen = []

    async def process(report_id, user):
        seen.append(report_id)

    monkeypatch.setattr(workflow, "process_intake", process)
    monkeypatch.setattr(review, "prepare_review", process)
    body = {"text": "VPN broke after I changed my password", "submissionKey": str(uuid4())}
    created = await client.post("/api/intake", json=body)
    assert created.status_code == 200
    assert created.json() == {"id": created.json()["id"]}
    assert seen == [created.json()["id"]] * 2


async def test_evaluation_report_is_operator_only_and_read_at_request_time(
    client, monkeypatch, tmp_path
):
    path = tmp_path / "arms-results.json"
    monkeypatch.setattr(api, "EVALUATION_PATH", path)
    assert (await client.get("/api/evaluation")).status_code == 403
    await client.post("/api/session", json={"userId": "alex"})
    assert (await client.get("/api/evaluation")).json() == {"available": False}
    report = {
        "runDate": "2026-09-06T00:00:00+00:00",
        "model": "gpt-test",
        "rows": [{"arm": "single", "split": "dev"}],
        "cases": {"single": {"dev": [{"id": "dev-001"}]}},
    }
    path.write_text(json.dumps(report))
    payload = (await client.get("/api/evaluation")).json()
    assert payload == {"available": True, **{k: v for k, v in report.items() if k != "cases"}}
    with_cases = (await client.get("/api/evaluation", params={"cases": "1"})).json()
    assert with_cases["cases"] == report["cases"] and with_cases["available"] is True
    path.unlink()
    assert (await client.get("/api/evaluation")).json() == {"available": False}
