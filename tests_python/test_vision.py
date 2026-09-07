"""Vision intake: a chat screenshot is staged before review, served back, and shown to the model."""

import base64
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from relay import cli, workflow
from relay.agents import build_pipeline
from relay.agents.compose import compose_decision
from relay.agents.intake import run_intake
from relay.agents.orchestrator import run_deterministic
from relay.agents.single import run_single_agent
from relay.agents.prompts import REVIEWER_PROMPT, TRIAGE_PROMPT
from relay.agents.runtime import ModelRuntime, Rejected
from relay.agents.schemas import (
    Budget,
    PipelineContext,
    PipelineResult,
    ReviewerOutput,
    RoutingProposal,
    SingleAgentOutput,
)
from relay.api import app
from relay.attachments import (
    MAX_FILE,
    intake_images,
    process_attachments,
    stage_intake_image,
    validate_file,
)
from relay.cli import probe_png, seed_demo
from relay.connector import DemoConnector
from relay.db import query, transaction
from relay.intake_evidence import apply_extraction
from relay.intake_prompt import INTAKE_PROMPT
from relay.jobs import process_operation
from relay.model import Extraction, extract_live, validate_extraction
from relay.policy import decide
from relay.review import approve_review, prepare_review, view_review
from relay.workflow import get_report, intake, process_intake
from starlette.datastructures import FormData

pytestmark = pytest.mark.usefixtures("isolated_db")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
TEXT = "My external monitor is flickering. I restarted the laptop, but it still happens. Only I am affected. Please send this to IT support."
# Through real demo retrieval this reaches a review; the monitor text above offers a procedure.
CHAT = "VPN broke after I changed my password"
ORIGIN = "http://127.0.0.1:3000"
NOT_SENT = "Your screenshot was used to understand the issue but this Jira request type does not accept attachments, so it will not be sent."


@pytest_asyncio.fixture
async def client():
    await seed_demo()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        yield client


@pytest_asyncio.fixture
async def owner():
    await seed_demo()
    return (await query("SELECT * FROM users WHERE id='maya'")).rows[0]


def multipart(text=CHAT, *, filename="screen.png", content=PNG, key=None, **extra):
    payload = {"text": text, "submissionKey": key or str(uuid4()), **extra}
    return {
        "data": {"payload": json.dumps(payload)},
        "files": {"image": (filename, content, "image/png")},
    }


async def no_sources(text, user):
    return []


async def user_message(report_id):
    return (
        await query(
            "SELECT id FROM messages WHERE report_id=$1 AND role='user' ORDER BY created_at",
            [report_id],
        )
    ).rows[0]


async def attachments(report_id):
    return (
        await query(
            "SELECT * FROM report_attachments WHERE report_id=$1 ORDER BY created_at", [report_id]
        )
    ).rows


class RecordingProvider(DemoConnector):
    attachments_allowed = True

    def __init__(self):
        self.uploads = []

    async def review_form(self, d):
        form = await super().review_form(d)
        form["attachmentsAllowed"] = self.attachments_allowed
        return form

    async def attach(self, key, filename, content, content_type):
        self.uploads.append((filename, bytes(content), content_type))
        return await super().attach(key, filename, content, content_type)


# --- staging and transport -------------------------------------------------------------


async def test_multipart_intake_stages_the_screenshot_on_the_user_message(client, monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("demo mode must not call the model")

    monkeypatch.setattr(workflow, "extract_live", never)
    created = await client.post("/api/intake", **multipart())
    assert created.status_code == 200, created.text
    id = created.json()["id"]
    [row] = await attachments(id)
    message = await user_message(id)
    assert (row["origin"], row["state"], row["message_id"]) == (
        "intake_image",
        "staged",
        message["id"],
    )
    assert (row["filename"], row["content_type"], row["size"]) == (
        "screen.png",
        "image/png",
        len(PNG),
    )
    assert bytes(row["content"]) == PNG
    assert (await query("SELECT status FROM agent_runs WHERE report_id=$1", [id])).rows == [
        {"status": "skipped"}
    ]
    review = (await client.get("/api/review", params={"id": id})).json()["review"]
    assert [a["id"] for a in review["attachments"]] == [row["id"]]
    plain = await client.post("/api/intake", json={"text": CHAT, "submissionKey": str(uuid4())})
    assert plain.status_code == 200 and await attachments(plain.json()["id"]) == []


async def test_the_image_is_staged_in_the_intake_transaction_before_any_review(owner):
    id = await intake(
        {"text": TEXT, "submissionKey": str(uuid4())}, owner, image=("shot.jpg", JPEG, "image/jpeg")
    )
    assert (await query("SELECT 1 FROM ticket_reviews WHERE report_id=$1", [id])).rows == []
    [image] = await intake_images(id)
    assert image == {
        "id": image["id"],
        "filename": "shot.jpg",
        "contentType": "image/jpeg",
        "size": len(JPEG),
        "messageId": (await user_message(id))["id"],
        "hasContent": True,
    }


async def test_replaying_the_submission_key_does_not_stage_a_second_image(client):
    body = multipart(key=str(uuid4()))
    first = await client.post("/api/intake", **body)
    second = await client.post("/api/intake", **body)
    assert first.status_code == 200 and second.json() == {"id": first.json()["id"]}
    assert len(await attachments(first.json()["id"])) == 1


@pytest.mark.parametrize(
    "filename,content,message",
    [
        ("fake.png", b"not a png", "does not match its PNG extension"),
        ("notes.pdf", b"%PDF-1.4 synthetic", "Attach a PNG or JPEG screenshot"),
        ("notes.txt", b"hello", "Attach a PNG or JPEG screenshot"),
    ],
)
async def test_non_screenshot_uploads_are_rejected_without_a_report(
    client, filename, content, message
):
    res = await client.post("/api/intake", **multipart(filename=filename, content=content))
    assert res.status_code == 400 and message in res.json()["error"]
    assert (await query("SELECT count(*)::int n FROM reports")).rows[0]["n"] == 0


async def test_oversized_or_unbounded_multipart_bodies_are_refused_before_parsing(client):
    huge = PNG + b"\x00" * (MAX_FILE + 32 * 1024)
    res = await client.post("/api/intake", **multipart(content=huge))
    assert (res.status_code, res.json()) == (400, {"error": "Message too large"})

    async def chunked():
        yield b'--x\r\nContent-Disposition: form-data; name="payload"\r\n\r\n{}\r\n--x--\r\n'

    res = await client.post(
        "/api/intake",
        content=chunked(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert (res.status_code, res.json()) == (400, {"error": "Message too large"})
    res = await client.post("/api/intake", **multipart(pad="y" * 15000))
    assert (res.status_code, res.json()) == (400, {"error": "Message too large"})
    assert (await query("SELECT count(*)::int n FROM reports")).rows[0]["n"] == 0


async def test_a_screenshot_needs_words(client, owner):
    with pytest.raises(ValueError, match="Describe the issue in words as well as the screenshot."):
        await intake(
            {"text": " ", "reportId": str(uuid4()), "submissionKey": str(uuid4())},
            owner,
            image=("shot.png", PNG, "image/png"),
        )
    id = (
        await client.post("/api/intake", json={"text": CHAT, "submissionKey": str(uuid4())})
    ).json()["id"]
    res = await client.post("/api/intake", **multipart(text="", reportId=id))
    assert res.status_code == 400 and "words as well as the screenshot" in res.json()["error"]
    res = await client.post("/api/intake", **multipart(text=""))
    assert res.status_code == 400
    assert len(await attachments(id)) == 0


async def test_intake_images_share_the_attachment_quotas(owner):
    id = await intake(
        {"text": TEXT, "submissionKey": str(uuid4())}, owner, image=("1.png", PNG, "image/png")
    )
    message_id = (await user_message(id))["id"]
    async with transaction() as db:
        for n in (2, 3):
            await stage_intake_image(db, id, owner, message_id, f"{n}.png", PNG)
        with pytest.raises(ValueError, match="three files"):
            await stage_intake_image(db, id, owner, message_id, "4.png", PNG)
    assert len(await attachments(id)) == 3
    other = await intake({"text": TEXT, "submissionKey": str(uuid4())}, owner)
    # Quota is calculated from persisted declared sizes, as in the review upload tests.
    for _ in range(10):
        file_id = str(uuid4())
        await query(
            "INSERT INTO report_attachments(id,report_id,filename,provider_filename,content_type,size,content) VALUES($1,$2,'quota.txt',$3,'text/plain',$4,$5)",
            [file_id, other, file_id + "-quota.txt", MAX_FILE, b"quota placeholder"],
        )
    third = await intake({"text": TEXT, "submissionKey": str(uuid4())}, owner)
    async with transaction() as db:
        with pytest.raises(ValueError, match="storage is full"):
            await stage_intake_image(
                db, third, owner, (await user_message(third))["id"], "x.png", PNG
            )


async def test_attachment_bytes_are_served_to_the_owner_and_operators_only(client):
    id = (await client.post("/api/intake", **multipart())).json()["id"]
    detail = (await client.get("/api/reports", params={"id": id})).json()
    message = next(m for m in detail["messages"] if m["role"] == "user")
    image = message["image"]
    assert image == {
        "id": image["id"],
        "filename": "screen.png",
        "contentType": "image/png",
        "size": len(PNG),
        "url": f"/api/attachments?reportId={id}&id={image['id']}",
        "hasContent": True,
    }
    assert all(m["image"] is None for m in detail["messages"] if m["role"] == "assistant")
    served = await client.get(image["url"])
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "private, no-store"
    assert (
        await client.get("/api/attachments", params={"reportId": id, "id": "bad"})
    ).status_code == 400
    assert (
        await client.get("/api/attachments", params={"reportId": id, "id": str(uuid4())})
    ).status_code == 404
    await client.post("/api/session", json={"userId": "jordan"})
    assert (await client.get(image["url"])).status_code == 404
    await client.post("/api/session", json={"userId": "alex"})
    assert (await client.get(image["url"])).content == PNG
    await query(
        "UPDATE report_attachments SET content=NULL,state='succeeded' WHERE id=$1", [image["id"]]
    )
    assert (await client.get(image["url"])).status_code == 404
    operator_view = (await client.get("/api/reports", params={"id": id})).json()
    assert (
        next(m for m in operator_view["messages"] if m["role"] == "user")["image"]["hasContent"]
        is False
    )


# --- review stage ------------------------------------------------------------------------


async def staged_report(owner, image=("shot.png", PNG, "image/png")):
    id = await intake({"text": TEXT, "submissionKey": str(uuid4())}, owner, image=image)
    await process_intake(id, owner, {"retrieve": no_sources})
    assert (await get_report(id, owner))["state"] == "review_pending"
    return id


async def test_review_drops_the_screenshot_when_the_request_type_refuses_attachments(owner):
    provider = RecordingProvider()
    provider.attachments_allowed = False
    id = await staged_report(owner)
    await prepare_review(id, owner, provider=provider)
    assert (await get_report(id, owner))["state"] == "awaiting_approval"
    assert await intake_images(id) == [] and await attachments(id) == []
    bodies = [
        m["body"]
        for m in (
            await query("SELECT body FROM messages WHERE report_id=$1 AND role='assistant'", [id])
        ).rows
    ]
    assert NOT_SENT in bodies
    assert (await view_review(id, owner))["attachments"] == []


async def test_the_staged_screenshot_survives_review_and_is_delivered_after_approval(owner):
    provider = RecordingProvider()
    id = await staged_report(owner)
    await prepare_review(id, owner, provider=provider)
    review = await view_review(id, owner)
    [image] = await intake_images(id)
    assert [a["id"] for a in review["attachments"]] == [image["id"]]
    assert NOT_SENT not in [
        m["body"] for m in (await query("SELECT body FROM messages WHERE report_id=$1", [id])).rows
    ]
    await approve_review(id, owner, review["version"], provider)
    [operation] = (await query("SELECT * FROM connector_operations WHERE report_id=$1", [id])).rows
    assert operation["payload"]["attachmentIds"] == [image["id"]]
    await process_operation(operation["id"], provider)
    await process_attachments(provider)
    [row] = await attachments(id)
    assert provider.uploads == [(row["provider_filename"], PNG, "image/png")]
    assert row["state"] == "succeeded" and row["content"] is None
    assert (await intake_images(id))[0]["hasContent"] is False


# --- the intake model sees the image ---------------------------------------------------

SETTINGS = {"effort": "high", "maxOutputTokens": 4096}
USER = {
    "id": "maya",
    "name": "Maya Chen",
    "role": "employee",
    "location": "San Francisco",
    "device": "MacBook",
    "scope": "sf",
    "external_account": None,
}
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
IMAGE = {"data_url": DATA_URL, "detail": "auto", "attachmentId": "att-1"}
# Names no service: only the screenshot does.
MESSAGE = "It keeps disconnecting every few minutes and I cannot get anything done."
EXTRACTED = {
    "summary": "Connection keeps disconnecting every few minutes",
    "service": None,
    "serviceQuote": None,
    "symptomQuote": "It keeps disconnecting every few minutes",
    "impactQuote": None,
    "urgencyQuote": None,
    "deviceQuote": None,
    "startedQuote": None,
    "workaroundQuote": None,
    "attemptedStepsQuotes": [],
    "supportRequestQuote": None,
    "procedureAttemptedQuote": None,
    "securityQuote": None,
    "evidenceIds": ["m1"],
}
SEEN = {
    "imageObservations": [
        "GlobalProtect client window with a red disconnected status",
        "An error banner below the connect button",
    ],
    "imageText": "GlobalProtect\nStatus: Disconnected\nGateway unreachable",
    "imageService": "vpn",
}


def extraction(**overrides):
    return Extraction(**{**EXTRACTED, **overrides})


def completed(parsed):
    return SimpleNamespace(
        status="completed",
        output=[],
        output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=30, output_tokens=40),
    )


def mock_client(parse):
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return client


def runtime(parse):
    return ModelRuntime(
        SETTINGS,
        Budget(maxModelCalls=2, maxToolCalls=0, maxTotalTokens=20000, maxSeconds=75),
        model="gpt-test",
        client_factory=lambda: mock_client(parse),
    )


def context(image=None, pipeline="multi"):
    return PipelineContext(
        report_id="r1",
        text=MESSAGE,
        message_ids=["m1"],
        sources=[],
        user=USER,
        clarifications=0,
        procedure=None,
        settings=SETTINGS,
        pipeline=pipeline,
        image=image,
    )


def parts(kwargs):
    return [part["type"] for part in kwargs["input"][1]["content"]]


async def test_image_fields_are_only_accepted_when_an_image_was_provided():
    data = validate_extraction({**EXTRACTED, **SEEN}, MESSAGE, ["m1"], has_image=True)
    assert data["imageObservations"] == SEEN["imageObservations"]
    assert (data["imageText"], data["imageService"]) == (SEEN["imageText"], "vpn")
    plain = validate_extraction(EXTRACTED, MESSAGE, ["m1"])
    assert (plain["imageObservations"], plain["imageText"], plain["imageService"]) == (
        [],
        None,
        None,
    )
    for change in (
        {"imageObservations": ["A dialog"]},
        {"imageText": "401"},
        {"imageService": "vpn"},
    ):
        with pytest.raises(ValueError, match="Model described an image that was not provided."):
            validate_extraction({**EXTRACTED, **change}, MESSAGE, ["m1"])
    with pytest.raises(ValueError):
        Extraction(**{**EXTRACTED, "imageObservations": ["x" * 201]})
    with pytest.raises(ValueError):
        Extraction(**{**EXTRACTED, "imageObservations": ["x"] * 9})
    with pytest.raises(ValueError):
        Extraction(**{**EXTRACTED, "imageText": "x" * 2001})
    # Screenshot text never stands in for a quote from the message.
    with pytest.raises(ValueError, match="unsupported evidence"):
        validate_extraction(
            {**EXTRACTED, **SEEN, "service": "vpn", "serviceQuote": "GlobalProtect"},
            MESSAGE,
            ["m1"],
            has_image=True,
        )


async def test_extract_live_sends_the_image_after_the_text_and_validates_with_it(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    parse = AsyncMock(return_value=completed(extraction(**SEEN)))
    monkeypatch.setattr("relay.model.live_client", lambda: mock_client(parse))
    result = await extract_live(MESSAGE, ["m1"], image=IMAGE)
    content = parse.call_args.kwargs["input"][1]["content"]
    assert json.loads(content[0]["text"]) == {
        "message": MESSAGE,
        "evidenceIds": ["m1"],
        "approvedProcedure": None,
    }
    assert content[1] == {"type": "input_image", "image_url": DATA_URL, "detail": "auto"}
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert result["data"]["imageText"] == SEEN["imageText"]
    assert result["usage"] == {"input": 30, "output": 40}
    parse.reset_mock()
    with pytest.raises(ValueError, match="image that was not provided"):
        await extract_live(MESSAGE, ["m1"])
    assert isinstance(parse.call_args.kwargs["input"][1]["content"], str)


async def test_run_intake_shows_the_image_it_has_and_rejects_phantom_observations():
    parse = AsyncMock(return_value=completed(extraction(**SEEN)))
    rt = runtime(parse)
    seen = await run_intake(context(), rt, image=IMAGE)
    assert parts(parse.call_args.kwargs) == ["input_text", "input_image"]
    assert parse.call_args.kwargs["input"][1]["content"][1]["image_url"].startswith(
        "data:image/png;base64,"
    )
    assert seen["imageObservations"] == SEEN["imageObservations"]
    assert [s.status for s in rt.steps] == ["ok"]
    parse = AsyncMock(return_value=completed(extraction(**SEEN)))
    rt = runtime(parse)
    with pytest.raises(Rejected, match="image that was not provided"):
        await run_intake(context(), rt)
    assert parts(parse.call_args.kwargs) == ["input_text"]
    assert [s.status for s in rt.steps] == ["rejected", "rejected"]


async def test_observations_become_image_evidence_and_screenshot_text_only_adds_a_candidate(
    monkeypatch,
):
    monkeypatch.setenv("APP_MODE", "live")
    d = apply_extraction(decide(MESSAGE, [], USER), {**EXTRACTED, **SEEN}, attachment_id="att-1")
    assert d["facts"]["imageEvidence"] == {
        "value": "\n".join(SEEN["imageObservations"]),
        "origin": "image",
        "evidenceIds": ["att-1"],
    }
    assert "imageEvidence" not in apply_extraction(decide(MESSAGE, [], USER), EXTRACTED)["facts"]
    bare = decide(MESSAGE, [], USER, scoring="v1")
    assert bare["alternatives"] == [] and bare["team"] == "Service Desk"
    ctx = context(image=IMAGE, pipeline="deterministic")
    rt = runtime(AsyncMock())
    result = PipelineResult(
        run=rt.finish(pipeline="deterministic", scoring="v1", status="completed", outcome={}),
        extraction={**EXTRACTED, **SEEN},
        summary=EXTRACTED["summary"],
    )
    d = compose_decision(ctx, result, rt, scoring="v1")
    assert [a["team"] for a in d["alternatives"]] == ["Network"]
    assert d["facts"]["symptom"] == bare["facts"]["symptom"]
    assert d["facts"]["imageEvidence"]["evidenceIds"] == ["att-1"]
    assert not any(
        "GlobalProtect" in str(f["value"]) for k, f in d["facts"].items() if k != "imageEvidence"
    )


@pytest_asyncio.fixture
async def live(owner, monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    return owner


async def staged_live(live):
    id = await intake(
        {"text": MESSAGE, "submissionKey": str(uuid4())}, live, image=("shot.png", PNG, "image/png")
    )
    [staged] = await intake_images(id)
    return id, staged


def patch_runtime(monkeypatch, parse):
    runtime_cls = workflow.ModelRuntime
    monkeypatch.setattr(
        workflow,
        "ModelRuntime",
        lambda *args, **kwargs: runtime_cls(
            *args, **kwargs, client_factory=lambda: mock_client(parse)
        ),
    )


async def test_the_deterministic_arm_hands_the_staged_screenshot_to_extraction(live, monkeypatch):
    monkeypatch.setenv("RELAY_PIPELINE", "deterministic")
    monkeypatch.setenv("RELAY_IMAGE_DETAIL", "low")
    id, staged = await staged_live(live)
    calls = []

    async def extract(text, message_ids, procedure, image=None):
        calls.append(image)
        return {
            "usage": {"input": 30, "output": 40},
            "data": {**EXTRACTED, **SEEN, "evidenceIds": message_ids},
        }

    await process_intake(id, live, {"retrieve": no_sources, "extract_live": extract})
    assert calls == [{"data_url": DATA_URL, "detail": "low", "attachmentId": staged["id"]}]
    report = await get_report(id, live)
    decision = report["decision"]
    assert decision["facts"]["imageEvidence"] == {
        "value": "\n".join(SEEN["imageObservations"]),
        "origin": "image",
        "evidenceIds": [staged["id"]],
    }
    assert (decision["team"], decision["service"]) == ("Network", "vpn")
    assert decision["promptVersions"] == {"intake": "relay-intake-v3"}
    assert report["state"] == "review_pending"


async def test_the_deterministic_arm_calls_extraction_as_before_without_a_screenshot(live, monkeypatch):
    monkeypatch.setenv("RELAY_PIPELINE", "deterministic")
    id = await intake({"text": MESSAGE, "submissionKey": str(uuid4())}, live)
    calls = []

    async def extract(text, message_ids, procedure):
        calls.append(text)
        return {
            "usage": {"input": 1, "output": 1},
            "data": {**EXTRACTED, "evidenceIds": message_ids},
        }

    await process_intake(id, live, {"retrieve": no_sources, "extract_live": extract})
    assert calls == [MESSAGE]
    assert "imageEvidence" not in (await get_report(id, live))["decision"]["facts"]


async def test_the_single_arm_validates_against_the_image_it_was_shown(live, monkeypatch):
    id, staged = await staged_live(live)
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        sent = json.loads(kwargs["input"][1]["content"][0]["text"])
        return completed(
            SingleAgentOutput(
                **EXTRACTED,
                **SEEN,
                team="Network",
                abstain=True,
                blockedQuote=None,
                broadImpactQuote=None,
                rationale="The message names no service; the screenshot shows GlobalProtect.",
                probability=0.4,
                citedSourceIds=[],
            ).model_copy(update={"evidenceIds": sent["evidenceIds"]})
        )

    patch_runtime(monkeypatch, parse)
    await process_intake(
        id, live, {"retrieve": no_sources, "pipeline": build_pipeline("single", extract=None)}
    )
    [call] = calls
    assert parts(call) == ["input_text", "input_image"]
    decision = (await get_report(id, live))["decision"]
    assert decision["facts"]["imageEvidence"]["evidenceIds"] == [staged["id"]]
    run = (await query("SELECT status,outcome FROM agent_runs WHERE report_id=$1", [id])).rows[0]
    assert (run["status"], run["outcome"]["extraction"]) == ("completed", "validated")


async def test_only_the_intake_role_sees_the_screenshot_in_the_multi_arm(live, monkeypatch):
    id, staged = await staged_live(live)
    seen = {}

    async def parse(**kwargs):
        role = {INTAKE_PROMPT: "intake", TRIAGE_PROMPT: "triage", REVIEWER_PROMPT: "reviewer"}[
            kwargs["input"][0]["content"]
        ]
        seen[role] = parts(kwargs)
        sent = json.loads(kwargs["input"][1]["content"][0]["text"])
        if role == "intake":
            return completed(extraction(**SEEN, evidenceIds=sent["evidenceIds"]))
        if role == "triage":
            return completed(
                RoutingProposal(
                    service="vpn",
                    team="Network",
                    abstain=False,
                    blockedQuote=None,
                    broadImpactQuote=None,
                    securityQuote=None,
                    rationale="The screenshot shows GlobalProtect disconnected.",
                    probability=0.8,
                    citedSourceIds=[],
                )
            )
        return completed(
            ReviewerOutput(
                verdict="accept", agreementProbability=0.8, issues=[], securityQuote=None
            )
        )

    patch_runtime(monkeypatch, parse)
    await process_intake(
        id, live, {"retrieve": no_sources, "pipeline": build_pipeline("multi", extract=None)}
    )
    assert seen == {
        "intake": ["input_text", "input_image"],
        "triage": ["input_text"],
        "reviewer": ["input_text"],
    }
    decision = (await get_report(id, live))["decision"]
    assert decision["facts"]["imageEvidence"]["evidenceIds"] == [staged["id"]]
    assert (decision["team"], decision["service"]) == ("Network", "vpn")


async def test_a_cleared_screenshot_is_not_shown_to_the_model(live, monkeypatch):
    monkeypatch.setenv("RELAY_PIPELINE", "deterministic")
    id, staged = await staged_live(live)
    await query(
        "UPDATE report_attachments SET content=NULL,state='expired' WHERE id=$1", [staged["id"]]
    )
    calls = []

    async def extract(text, message_ids, procedure, image=None):
        calls.append(image)
        return {
            "usage": {"input": 1, "output": 1},
            "data": {**EXTRACTED, "evidenceIds": message_ids},
        }

    await process_intake(id, live, {"retrieve": no_sources, "extract_live": extract})
    assert calls == [None]


# --- preflight ---------------------------------------------------------------------------


async def test_the_preflight_probe_is_a_real_png():
    png = probe_png()
    assert validate_file("probe.png", png) == "image/png"
    assert png.endswith(b"IEND\xaeB`\x82")
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (8, 8)


async def test_preflight_reports_whether_the_model_described_the_probe(monkeypatch, capsys):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    calls = []

    async def extract(text, source_ids, approved_procedure=None, image=None):
        calls.append(image)
        return {
            "data": {**EXTRACTED, **(SEEN if image else {})},
            "usage": {"input": 1, "output": 1},
        }

    async def embed(text):
        return [0.0] * 256

    async def demo():
        return DemoConnector()

    monkeypatch.setattr("relay.model.extract_live", extract)
    monkeypatch.setattr("relay.model.embed", embed)
    monkeypatch.setattr("relay.connector.connector", demo)
    await cli.run(SimpleNamespace(command="preflight"))
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert lines[-1] == {"vision": True}
    assert lines[-2]["structuredOutput"] is True
    assert calls[0] is None and calls[1]["data_url"].startswith("data:image/png;base64,")


# --- hardening: ordering, form release, image loading, trace marker ----------------------


async def test_the_screenshot_notice_lands_before_the_ready_message(owner):
    provider = RecordingProvider()
    provider.attachments_allowed = False
    id = await staged_report(owner)
    await prepare_review(id, owner, provider=provider)
    rows = (
        await query(
            "SELECT body,created_at FROM messages WHERE report_id=$1 AND role='assistant' ORDER BY created_at,id",
            [id],
        )
    ).rows
    bodies = [r["body"] for r in rows]
    notice = bodies.index(NOT_SENT)
    ready = next(i for i, body in enumerate(bodies) if body.startswith("Your ticket draft is ready"))
    assert notice < ready
    assert rows[notice]["created_at"] < rows[ready]["created_at"]


async def test_the_parsed_multipart_form_is_closed_after_intake(client, monkeypatch):
    closed = []
    original = FormData.close

    async def close(self):
        closed.append(True)
        await original(self)

    monkeypatch.setattr(FormData, "close", close)
    created = await client.post("/api/intake", **multipart())
    assert created.status_code == 200 and closed == [True]
    rejected = await client.post("/api/intake", **multipart(filename="fake.png", content=b"nope"))
    assert rejected.status_code == 400 and closed == [True, True]


async def test_the_screenshot_is_only_loaded_for_the_live_pipeline(owner, monkeypatch):
    monkeypatch.setenv("RELAY_PIPELINE", "deterministic")
    calls = []
    real = workflow.intake_image

    async def spy(report_id):
        calls.append(report_id)
        return await real(report_id)

    monkeypatch.setattr(workflow, "intake_image", spy)
    id = await intake(
        {"text": TEXT, "submissionKey": str(uuid4())}, owner, image=("shot.png", PNG, "image/png")
    )
    await process_intake(id, owner, {"retrieve": no_sources})
    assert calls == [] and (await get_report(id, owner))["state"] == "review_pending"
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    live_id, staged = await staged_live(owner)
    seen = []

    async def extract(text, message_ids, procedure, image=None):
        seen.append(image)
        return {
            "usage": {"input": 1, "output": 1},
            "data": {**EXTRACTED, **SEEN, "evidenceIds": message_ids},
        }

    await process_intake(live_id, owner, {"retrieve": no_sources, "extract_live": extract})
    assert calls == [live_id] and seen[0]["attachmentId"] == staged["id"]


async def test_the_intake_step_marks_an_attached_image_in_every_arm(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")

    async def extract(text, message_ids, procedure, image=None):
        data = {**EXTRACTED, **(SEEN if image else {}), "evidenceIds": message_ids}
        return {"usage": {"input": 1, "output": 1}, "data": data}

    rt = runtime(AsyncMock())
    await run_deterministic(context(image=IMAGE, pipeline="deterministic"), rt, extract)
    assert rt.steps[0].inputSummary.endswith(" [+image]")
    rt = runtime(AsyncMock())
    await run_deterministic(context(pipeline="deterministic"), rt, extract)
    assert "[+image]" not in rt.steps[0].inputSummary

    def single(**seen):
        return completed(
            SingleAgentOutput(
                **EXTRACTED,
                **seen,
                team="Service Desk",
                abstain=True,
                blockedQuote=None,
                broadImpactQuote=None,
                rationale="No service is named.",
                probability=0.4,
                citedSourceIds=[],
            )
        )

    rt = runtime(AsyncMock(return_value=single(**SEEN)))
    await run_single_agent(context(image=IMAGE, pipeline="single"), rt)
    assert rt.steps[0].role == "intake" and rt.steps[0].inputSummary.endswith(" [+image]")
    rt = runtime(AsyncMock(return_value=single()))
    await run_single_agent(context(pipeline="single"), rt)
    assert "[+image]" not in rt.steps[0].inputSummary

    rt = runtime(AsyncMock(return_value=completed(extraction(**SEEN))))
    await run_intake(context(image=IMAGE), rt, image=IMAGE)
    assert rt.steps[0].inputSummary.endswith(" [+image]")
    rt = runtime(AsyncMock(return_value=completed(extraction())))
    await run_intake(context(), rt)
    assert "[+image]" not in rt.steps[0].inputSummary
