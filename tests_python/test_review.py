"""Owner approval and attachment delivery boundaries against isolated PostgreSQL."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from relay.attachments import (
    MAX_FILE,
    expire_files,
    process_attachments,
    remove_file,
    stage_file,
    validate_file,
)
from relay.cli import seed_demo
from relay.connector import ConnectorError, DemoConnector
from relay.db import query, transaction
from relay.jobs import process_operation
from relay.review import approve_review, prepare_review, save_review, view_review
from relay.workflow import enqueue, get_report, intake, process_intake

pytestmark = pytest.mark.asyncio
TEXT = "My external monitor is flickering. I restarted the laptop, but it still happens. Only I am affected. Please send this to IT support."


@pytest_asyncio.fixture
async def owner(isolated_db):
    await seed_demo()
    return (await query("SELECT * FROM users WHERE id='maya'")).rows[0]


async def no_sources(text, user):
    return []


async def make_review(owner, provider=None, verifier=None):
    report_id = await intake({"text": TEXT, "submissionKey": str(uuid4())}, owner)
    await process_intake(report_id, owner, {"retrieve": no_sources})
    assert (await get_report(report_id, owner))["state"] == "review_pending"
    assert await operations(report_id) == []
    await prepare_review(report_id, owner, provider=provider or DemoConnector(), verifier=verifier)
    return report_id, await view_review(report_id, owner)


async def operations(report_id):
    return (
        await query(
            "SELECT * FROM connector_operations WHERE report_id=$1 ORDER BY created_at", [report_id]
        )
    ).rows


async def test_known_secret_patterns_in_edits_are_redacted_before_verification(owner):
    report_id, review = await make_review(owner)
    values = {**review["form"]["values"], "summary": "VPN password: synthetic-test-secret"}
    verifier = AsyncMock(
        return_value={
            "status": "passed",
            "issues": [],
            "checks": [],
            "model": "test",
            "promptVersion": "test",
        }
    )
    saved = await save_review(
        report_id, owner, review["version"], values, DemoConnector(), verifier
    )
    assert saved["form"]["values"]["summary"] == "VPN password: [REDACTED]"
    assert "synthetic-test-secret" not in str(verifier.call_args)
    stored = (
        await query("SELECT content FROM ticket_reviews WHERE report_id=$1", [report_id])
    ).rows[0]
    assert "synthetic-test-secret" not in str(stored)


async def file_row(file_id):
    return (await query("SELECT * FROM report_attachments WHERE id=$1", [file_id])).rows[0]


async def make_created(owner, provider, *, filename="evidence.txt", content=b"flicker evidence"):
    report_id, review = await make_review(owner, provider)
    review = await stage_file(report_id, owner, review["version"], filename, content)
    await approve_review(report_id, owner, review["version"], provider)
    op = (await operations(report_id))[0]
    await process_operation(op["id"], provider)
    assert (await get_report(report_id, owner))["provider_key"]
    return report_id, review["attachments"][0]["id"]


class RecordingProvider(DemoConnector):
    def __init__(self):
        self.creates = 0
        self.uploads = []
        self.remote_files = set()
        self.lose_response = False
        self.reject = False

    async def create(self, d):
        self.creates += 1
        return await super().create(d)

    async def attach(self, key, filename, content, content_type):
        self.uploads.append((key, filename, bytes(content), content_type))
        if self.reject:
            raise ConnectorError("Attachment permission denied", status=403)
        self.remote_files.add((key, filename, len(content)))
        if self.lose_response:
            raise ConnectorError("Upload response lost", ambiguous=True)
        return await super().attach(key, filename, content, content_type)

    async def attachment_exists(self, key, filename, size):
        return (key, filename, size) in self.remote_files


class FormProvider(RecordingProvider):
    schema_version = "v1"
    custom_required = False
    attachments_required = False
    attachments_allowed = True

    async def review_form(self, d):
        form = await super().review_form(d)
        form["schemaFingerprint"] += self.schema_version
        form["attachmentsRequired"] = self.attachments_required
        form["attachmentsAllowed"] = self.attachments_allowed
        if self.custom_required:
            form["fields"].append(
                {
                    "id": "asset",
                    "label": "Device asset tag",
                    "kind": "text",
                    "required": True,
                    "options": [],
                }
            )
        return form


async def test_draft_never_enqueues_or_creates_before_owner_approval(owner):
    provider = RecordingProvider()
    report_id, review = await make_review(owner, provider)
    assert review["state"] == "awaiting_approval"
    assert review["verification"]["status"] == "passed"
    assert provider.creates == 0
    await process_attachments(provider)
    assert provider.uploads == []
    assert await operations(report_id) == []
    await approve_review(report_id, owner, review["version"], provider)
    assert len(await operations(report_id)) == 1
    assert provider.creates == 0
    assert (await get_report(report_id, owner))["state"] == "submission_pending"


async def test_unavailable_verifier_cannot_be_approved_and_can_recover(owner):
    verifier = AsyncMock(side_effect=RuntimeError("offline"))
    report_id, review = await make_review(owner, verifier=verifier)
    assert review["verification"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="verification"):
        await approve_review(report_id, owner, review["version"], DemoConnector())
    assert await operations(report_id) == []
    review = await save_review(
        report_id, owner, review["version"], review["form"]["values"], DemoConnector()
    )
    assert review["verification"]["status"] == "passed"


async def test_missing_required_custom_field_saved_and_verified_before_approval(owner):
    provider = FormProvider()
    provider.custom_required = True
    report_id, review = await make_review(owner, provider)
    assert review["verification"]["status"] == "needs_changes"
    with pytest.raises(ValueError, match="verification"):
        await approve_review(report_id, owner, review["version"], provider)
    values = {**review["form"]["values"], "asset": "LAP-2205"}
    review = await save_review(report_id, owner, review["version"], values, provider)
    assert review["verification"]["status"] == "passed"
    await approve_review(report_id, owner, review["version"], provider)
    assert (await operations(report_id))[0]["payload"]["approvedPayload"]["requestFieldValues"][
        "asset"
    ] == "LAP-2205"


async def test_changed_jira_schema_requires_a_new_verified_version(owner):
    provider = FormProvider()
    report_id, review = await make_review(owner, provider)
    provider.schema_version = "v2"
    with pytest.raises(ValueError, match="form changed"):
        await approve_review(report_id, owner, review["version"], provider)
    assert await operations(report_id) == []
    refreshed = await save_review(
        report_id, owner, review["version"], review["form"]["values"], provider
    )
    assert refreshed["form"]["schemaFingerprint"].endswith("v2")
    await approve_review(report_id, owner, refreshed["version"], provider)


async def test_concurrent_and_repeated_approval_emit_one_create_and_one_event(owner):
    provider = DemoConnector()
    report_id, review = await make_review(owner, provider)
    results = await asyncio.gather(
        *[approve_review(report_id, owner, review["version"], provider) for _ in range(3)]
    )
    assert results == [report_id] * 3
    assert await approve_review(report_id, owner, review["version"], provider) == report_id
    assert len(await operations(report_id)) == 1
    assert (
        await query(
            "SELECT count(*) n FROM operator_events WHERE report_id=$1 AND kind='requester-approved'",
            [report_id],
        )
    ).rows[0]["n"] == 1


@pytest.mark.parametrize("actor_id", ["jordan", "alex"])
async def test_other_employee_and_operator_cannot_review_or_approve_for_owner(owner, actor_id):
    report_id, review = await make_review(owner)
    actor = (await query("SELECT * FROM users WHERE id=$1", [actor_id])).rows[0]
    for action in (
        lambda: view_review(report_id, actor),
        lambda: save_review(
            report_id, actor, review["version"], review["form"]["values"], DemoConnector()
        ),
        lambda: approve_review(report_id, actor, review["version"], DemoConnector()),
        lambda: stage_file(report_id, actor, review["version"], "file.txt", b"hello"),
    ):
        with pytest.raises(ValueError, match="Not found"):
            await action()
    assert await operations(report_id) == []


async def test_review_endpoints_do_not_cross_app_modes(owner, monkeypatch):
    report_id, review = await make_review(owner)
    monkeypatch.setenv("APP_MODE", "live")
    for action in (
        lambda: view_review(report_id, owner),
        lambda: approve_review(report_id, owner, review["version"], DemoConnector()),
        lambda: stage_file(report_id, owner, review["version"], "file.txt", b"hello"),
    ):
        with pytest.raises(ValueError, match="Not found"):
            await action()


async def test_edit_and_upload_each_invalidate_stale_approval_versions(owner):
    report_id, original = await make_review(owner)
    values = {**original["form"]["values"], "summary": "Dock-connected monitor keeps flickering"}
    edited = await save_review(report_id, owner, original["version"], values, DemoConnector())
    with pytest.raises(ValueError, match="changed in another window"):
        await approve_review(report_id, owner, original["version"], DemoConnector())
    staged = await stage_file(report_id, owner, edited["version"], "evidence.txt", b"evidence")
    with pytest.raises(ValueError, match="changed in another window"):
        await approve_review(report_id, owner, edited["version"], DemoConnector())
    assert staged["version"] == original["version"] + 2
    assert await operations(report_id) == []


async def test_required_attachment_and_disabled_attachment_forms(owner):
    provider = FormProvider()
    provider.attachments_required = True
    report_id, review = await make_review(owner, provider)
    with pytest.raises(ValueError, match="requires at least one attachment"):
        await approve_review(report_id, owner, review["version"], provider)
    review = await stage_file(report_id, owner, review["version"], "evidence.txt", b"evidence")
    await approve_review(report_id, owner, review["version"], provider)
    provider.attachments_required = False
    provider.attachments_allowed = False
    report_id, review = await make_review(owner, provider)
    with pytest.raises(ValueError, match="does not accept attachments"):
        await stage_file(report_id, owner, review["version"], "evidence.txt", b"evidence")


async def test_removing_attachment_checks_owner_report_and_version(owner):
    first_id, review = await make_review(owner)
    staged = await stage_file(first_id, owner, review["version"], "evidence.txt", b"evidence")
    file_id = staged["attachments"][0]["id"]
    other = (await query("SELECT * FROM users WHERE id='jordan'")).rows[0]
    with pytest.raises(ValueError, match="Not found"):
        await remove_file(first_id, other, staged["version"], file_id)
    second_id, second_review = await make_review(owner)
    with pytest.raises(ValueError, match="Not found"):
        await remove_file(second_id, owner, second_review["version"], file_id)
    with pytest.raises(ValueError, match="changed in another window"):
        await remove_file(first_id, owner, review["version"], file_id)
    removed = await remove_file(first_id, owner, staged["version"], file_id)
    assert removed["attachments"] == []
    assert removed["version"] == staged["version"] + 1


@pytest.mark.parametrize(
    "filename,content",
    [
        ("../evidence.txt", b"text"),
        ("folder\\evidence.txt", b"text"),
        ("file\n.txt", b"text"),
        ("payload.exe", b"text"),
        ("empty.txt", b""),
        ("huge.txt", b"x" * (MAX_FILE + 1)),
        ("fake.png", b"not png"),
        ("fake.jpg", b"not jpeg"),
        ("fake.pdf", b"not pdf"),
        ("binary.txt", b"nul\x00byte"),
        ("invalid.log", b"\xff"),
    ],
)
async def test_filename_size_content_and_extension_limits(filename, content):
    with pytest.raises(ValueError):
        validate_file(filename, content)


async def test_per_ticket_file_limit_and_unique_provider_names(owner):
    report_id, review = await make_review(owner)
    for _ in range(3):
        review = await stage_file(report_id, owner, review["version"], "evidence.txt", b"same name")
    assert len({f["providerFilename"] for f in review["attachments"]}) == 3
    with pytest.raises(ValueError, match="three files"):
        await stage_file(report_id, owner, review["version"], "fourth.txt", b"fourth")


@pytest.mark.parametrize("quota", ["owner", "global"])
async def test_storage_quota_counts_across_reports(owner, quota):
    report_id, review = await make_review(owner)
    other = (await query("SELECT * FROM users WHERE id='jordan'")).rows[0]
    storage_owner = owner if quota == "owner" else other
    storage_id, _ = await make_review(storage_owner)
    # Quota is calculated from persisted declared sizes. Tiny test blobs avoid
    # allocating hundreds of MiB solely to exercise the database quota query.
    for _ in range(10 if quota == "owner" else 100):
        file_id = str(uuid4())
        await query(
            "INSERT INTO report_attachments(id,report_id,filename,provider_filename,content_type,size,content) VALUES($1,$2,'quota.txt',$3,'text/plain',$4,$5)",
            [file_id, storage_id, file_id + "-quota.txt", MAX_FILE, b"quota placeholder"],
        )
    with pytest.raises(ValueError, match="storage is full"):
        await stage_file(report_id, owner, review["version"], "evidence.txt", b"evidence")


async def test_staged_file_is_local_until_approval_then_worker_uploads_exactly_once(owner):
    provider = RecordingProvider()
    report_id, review = await make_review(owner, provider)
    review = await stage_file(
        report_id, owner, review["version"], "evidence.txt", b"original bytes"
    )
    file_id = review["attachments"][0]["id"]
    await process_attachments(provider)
    assert provider.uploads == []
    assert (await file_row(file_id))["state"] == "staged"
    await approve_review(report_id, owner, review["version"], provider)
    assert (await file_row(file_id))["state"] == "pending"
    await process_attachments(provider)
    assert provider.uploads == []  # A confirmed Jira key is also required.
    await process_operation((await operations(report_id))[0]["id"], provider)
    await asyncio.gather(process_attachments(provider), process_attachments(provider))
    await process_attachments(provider)
    assert len(provider.uploads) == 1
    assert provider.uploads[0][1:] == (
        review["attachments"][0]["providerFilename"],
        b"original bytes",
        "text/plain",
    )
    stored = await file_row(file_id)
    assert stored["state"] == "succeeded" and stored["content"] is None
    with pytest.raises(ValueError, match="no longer editable"):
        await remove_file(report_id, owner, review["version"], file_id)


async def test_lost_attachment_response_reconciles_without_duplicate_upload(owner):
    provider = RecordingProvider()
    provider.lose_response = True
    report_id, file_id = await make_created(owner, provider)
    await process_attachments(provider)
    assert (await file_row(file_id))["state"] == "unknown"
    assert len(provider.uploads) == 1
    await query("UPDATE report_attachments SET next_attempt_at=now() WHERE id=$1", [file_id])
    await process_attachments(provider)
    assert (await file_row(file_id))["state"] == "succeeded"
    assert len(provider.uploads) == 1
    assert provider.creates == 1
    assert (await get_report(report_id, owner))["state"] == "created"


async def test_unconfirmed_lost_upload_is_checked_without_reposting(owner):
    provider = RecordingProvider()
    provider.lose_response = True
    _, file_id = await make_created(owner, provider)
    await process_attachments(provider)
    provider.remote_files.clear()
    for _ in range(6):
        await query("UPDATE report_attachments SET next_attempt_at=now() WHERE id=$1", [file_id])
        await process_attachments(provider)
    assert (await file_row(file_id))["state"] == "failed"
    assert len(provider.uploads) == 1


async def test_attachment_rejection_does_not_recreate_or_lose_the_created_ticket(owner):
    provider = RecordingProvider()
    provider.reject = True
    report_id, file_id = await make_created(owner, provider)
    before = (await get_report(report_id, owner))["provider_key"]
    await process_attachments(provider)
    await process_attachments(provider)
    assert (await file_row(file_id))["state"] == "failed"
    report = await get_report(report_id, owner)
    assert report["provider_key"] == before and report["state"] == "created"
    assert len(provider.uploads) == 1 and provider.creates == 1


async def test_expired_staged_file_blocks_approval_until_removed(owner):
    report_id, review = await make_review(owner)
    review = await stage_file(report_id, owner, review["version"], "old.txt", b"old evidence")
    file_id = review["attachments"][0]["id"]
    await query(
        "UPDATE report_attachments SET created_at=now()-interval '8 days' WHERE id=$1", [file_id]
    )
    await expire_files()
    assert (await file_row(file_id))["content"] is None
    with pytest.raises(ValueError, match="expired or unavailable"):
        await approve_review(report_id, owner, review["version"], DemoConnector())
    review = await remove_file(report_id, owner, review["version"], file_id)
    await approve_review(report_id, owner, review["version"], DemoConnector())


async def test_forced_enqueue_cannot_forge_missing_owner_approval(owner):
    report_id, review = await make_review(owner)
    report = await get_report(report_id, owner)
    forged = {
        "approvalVersion": review["version"],
        "approvedPayload": {"requestFieldValues": review["form"]["values"]},
    }
    with pytest.raises(ValueError, match="approval is required"):
        async with transaction() as db:
            await enqueue(db, report, owner, extra=forged)
    assert await operations(report_id) == []


async def test_worker_rejects_tampered_approved_payload_before_provider_call(owner):
    from psycopg.errors import CheckViolation

    provider = RecordingProvider()
    report_id, review = await make_review(owner, provider)
    await approve_review(report_id, owner, review["version"], provider)
    op = (await operations(report_id))[0]
    tampered = deepcopy(op["payload"])
    tampered["approvedPayload"]["requestFieldValues"]["summary"] = "Unapproved replacement"
    with pytest.raises(CheckViolation, match="Requester approval"):
        await query("UPDATE connector_operations SET payload=$2 WHERE id=$1", [op["id"], tampered])
    # A later invalidation of the approval is also checked by the worker itself.
    await query(
        "UPDATE ticket_reviews SET approved_payload=$2 WHERE report_id=$1",
        [report_id, tampered["approvedPayload"]],
    )
    await process_operation(op["id"], provider)
    assert provider.creates == 0
    assert (await operations(report_id))[0]["state"] == "failed"
    assert (await get_report(report_id, owner))["provider_key"] is None


async def test_worker_rejects_directly_inserted_operation_without_approval(owner):
    from psycopg.errors import CheckViolation

    provider = RecordingProvider()
    report_id, review = await make_review(owner, provider)
    op_id = str(uuid4())
    with pytest.raises(CheckViolation, match="Requester approval"):
        await query(
            "INSERT INTO connector_operations(id,report_id,operation_key,kind,state,payload,payload_hash) VALUES($1,$2,$3,'create','pending',$4,'forged')",
            [
                op_id,
                report_id,
                "create:" + report_id,
                {"approvalVersion": review["version"], "approvedPayload": {}},
            ],
        )
    await approve_review(report_id, owner, review["version"], provider)
    op_id = (await operations(report_id))[0]["id"]
    await query("UPDATE ticket_reviews SET approved_at=NULL WHERE report_id=$1", [report_id])
    await process_operation(op_id, provider)
    assert provider.creates == 0
    assert (await operations(report_id))[0]["state"] == "failed"
