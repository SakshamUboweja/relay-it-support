"""Vision intake: a chat screenshot is staged before review, served back, and shown to the model."""

import json
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from relay import workflow
from relay.api import app
from relay.attachments import (
    MAX_FILE,
    intake_images,
    process_attachments,
    stage_intake_image,
)
from relay.cli import seed_demo
from relay.connector import DemoConnector
from relay.db import query, transaction
from relay.jobs import process_operation
from relay.review import approve_review, prepare_review, view_review
from relay.workflow import get_report, intake, process_intake

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
