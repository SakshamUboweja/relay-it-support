"""Versioned ticket previews. Only owner approval can release an external create."""

import json
from copy import deepcopy
from uuid import uuid4

from .connector import connector, draft
from .db import connection, mode, query, transaction
from .policy import decide
from .verifier import verify_ticket
from .sanitize import sanitize


def pending_check():
    return {
        "status": "unavailable",
        "issues": [
            {"field": "summary", "message": "Verification is pending. Save and verify to retry."}
        ],
        "checks": [],
        "model": "pending",
        "promptVersion": "relay-verifier-v1",
        "usage": {"input": 0, "output": 0},
    }


async def owner_report(report_id, user, db=None, lock=False):
    rows = (
        await query(
            "SELECT * FROM reports WHERE id=$1 AND owner_id=$2 AND mode=$3"
            + (" FOR UPDATE" if lock else ""),
            [report_id, user["id"], mode()],
            db=db,
        )
    ).rows
    if not rows:
        raise ValueError("Not found")
    return rows[0]


async def row_for(report_id, db=None):
    rows = (await query("SELECT * FROM ticket_reviews WHERE report_id=$1", [report_id], db=db)).rows
    if not rows:
        raise ValueError("Not found")
    return rows[0]


async def view_review(report_id, user):
    report = await owner_report(report_id, user)
    row = await row_for(report_id)
    content = row["content"]
    files = (
        await query(
            "SELECT id,filename,provider_filename,size,content_type,state,last_error FROM report_attachments WHERE report_id=$1 ORDER BY created_at,id",
            [report_id],
        )
    ).rows
    return {
        "reportId": report_id,
        "version": row["version"],
        "state": "approved" if row["approved_at"] else "awaiting_approval",
        "form": content["form"],
        "team": content["candidate"]["team"],
        "priority": content["candidate"]["priority"],
        "verification": content["verification"],
        "approvedAt": row["approved_at"],
        "pipeline": report["decision"].get("pipeline"),
        "confidence": report["decision"].get("confidence"),
        "attachments": [
            {
                "id": f["id"],
                "filename": f["filename"],
                "providerFilename": f["provider_filename"],
                "size": f["size"],
                "contentType": f["content_type"],
                "state": f["state"],
                "error": f["last_error"],
            }
            for f in files
        ],
    }


def editable(row, report, version):
    if row["approved_at"] or report["provider_key"] or report["state"] != "awaiting_approval":
        raise ValueError("This ticket is no longer editable.")
    if row["version"] != version:
        raise ValueError(
            "This draft changed in another window. Reload the review before continuing."
        )
    if report["decision"]["visibility"] == "restricted":
        raise ValueError("Restricted reports cannot be sent to this Jira destination.")


async def validate_and_verify(report, user, content, provider, verifier):
    result = deepcopy(content)
    form = result["form"]
    candidate = {
        **result["candidate"],
        "summary": form["values"].get("summary", ""),
        "description": form["values"].get("description", ""),
    }
    result["candidate"] = candidate
    issues = []
    try:
        await provider.approved_payload(candidate, form["values"], form["requestTypeId"])
    except Exception as exc:
        issues.append({"field": "summary", "message": str(exc)})
    messages = (
        await query(
            "SELECT id,body FROM messages WHERE report_id=$1 AND role='user' ORDER BY created_at,id",
            [report["id"]],
        )
    ).rows
    # User edits are additional evidence, but never permission to bypass security policy.
    security = decide(
        "\n".join([m["body"] for m in messages] + [json.dumps(form["values"])]), [], user
    )
    if report["decision"]["visibility"] == "restricted" or security["visibility"] == "restricted":
        issues.append(
            {
                "field": "description",
                "message": "Security evidence requires local restricted review; this Jira destination is not permitted.",
            }
        )
    if issues:
        result["verification"] = {
            **pending_check(),
            "status": "needs_changes",
            "issues": issues,
            "model": "field-validation",
        }
    else:
        try:
            result["verification"] = await verifier(
                messages, candidate, form, result.get("userEdits")
            )
        except Exception:
            result["verification"] = {
                **pending_check(),
                "issues": [
                    {
                        "field": "summary",
                        "message": "The verifier is unavailable. Your draft is saved; save and verify to retry.",
                    }
                ],
            }
    return result


async def prepare_review(report_id, user, provider=None, verifier=None):
    verifier = verifier or verify_ticket
    async with connection() as lock:
        acquired = (
            await query(
                "SELECT pg_try_advisory_lock(hashtext($1)) locked", ["review:" + report_id], db=lock
            )
        ).rows[0]["locked"]
        if not acquired:
            return
        try:
            report = await owner_report(report_id, user)
            if report["state"] != "review_pending":
                return
            provider = provider or await connector()
            candidate = draft(report, user.get("external_account"))
            try:
                form = await provider.review_form(candidate)
            except Exception:
                # Keep a recoverable pending report, with no external operation.
                return
            content = {
                "candidate": candidate,
                "form": form,
                "verification": pending_check(),
                "userEdits": {},
            }
            content = await validate_and_verify(report, user, content, provider, verifier)
            async with transaction() as db:
                current = await owner_report(report_id, user, db=db, lock=True)
                if current["state"] != "review_pending":
                    return
                await query(
                    "INSERT INTO ticket_reviews(report_id,content) VALUES($1,$2) ON CONFLICT(report_id) DO NOTHING",
                    [report_id, content],
                    db=db,
                )
                await query(
                    "UPDATE reports SET state='awaiting_approval',updated_at=now() WHERE id=$1",
                    [report_id],
                    db=db,
                )
                await query(
                    "INSERT INTO messages VALUES($1,$2,'assistant',$3,now())",
                    [
                        str(uuid4()),
                        report_id,
                        "Your ticket draft is ready to review below. Check the fields, add any files, and approve when it looks right. Nothing has been sent to Jira.",
                    ],
                    db=db,
                )
        finally:
            await query("SELECT pg_advisory_unlock(hashtext($1))", ["review:" + report_id], db=lock)


async def save_review(report_id, user, version, values, provider=None, verifier=None):
    if not isinstance(values, dict) or len(json.dumps(values)) > 30000:
        raise ValueError("Invalid ticket fields")

    def redact(value):
        if isinstance(value, str):
            return sanitize(value)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    values = redact(values)
    provider = provider or await connector()
    verifier = verifier or verify_ticket
    report = await owner_report(report_id, user)
    row = await row_for(report_id)
    editable(row, report, version)
    fresh = await provider.review_form(row["content"]["candidate"])
    allowed = {f["id"] for f in fresh["fields"]}
    if set(values) - allowed:
        raise ValueError("The draft contains a field not offered by Jira. Reload the form.")
    for field in fresh["fields"]:
        if field.get("readOnly") and values.get(field["id"]) != fresh["values"].get(field["id"]):
            raise ValueError("Configured read-only fields cannot be changed.")
    content = deepcopy(row["content"])
    content["userEdits"].update(
        {k: v for k, v in values.items() if v != content["form"]["values"].get(k)}
    )
    content["form"] = {**fresh, "values": values}
    content["verification"] = pending_check()
    async with transaction() as db:
        report = await owner_report(report_id, user, db=db, lock=True)
        row = await row_for(report_id, db=db)
        editable(row, report, version)
        await query(
            "UPDATE ticket_reviews SET content=$2,version=version+1,updated_at=now() WHERE report_id=$1",
            [report_id, content],
            db=db,
        )
    checked = await validate_and_verify(report, user, content, provider, verifier)
    await query(
        "UPDATE ticket_reviews SET content=$2,updated_at=now() WHERE report_id=$1 AND version=$3 AND approved_at IS NULL",
        [report_id, checked, version + 1],
    )
    return await view_review(report_id, user)


async def approve_review(report_id, user, version, provider=None):
    from .workflow import enqueue

    report = await owner_report(report_id, user)
    row = await row_for(report_id)
    if row["approved_at"] and row["version"] == version:
        return report_id
    editable(row, report, version)
    content = row["content"]
    if content["verification"]["status"] != "passed":
        raise ValueError("Resolve the verification issues before approving this ticket.")
    provider = provider or await connector()
    fresh = await provider.review_form(content["candidate"])
    if fresh.get("schemaFingerprint") != content["form"].get("schemaFingerprint"):
        raise ValueError("Jira's form changed. Save and verify the draft again before approval.")
    payload = await provider.approved_payload(
        content["candidate"], content["form"]["values"], content["form"]["requestTypeId"]
    )
    async with transaction() as db:
        report = await owner_report(report_id, user, db=db, lock=True)
        row = await row_for(report_id, db=db)
        if row["approved_at"] and row["version"] == version:
            return report_id
        editable(row, report, version)
        if row["content"] != content:
            raise ValueError("Verification changed. Reload the review before approving.")
        files = (
            await query(
                "SELECT id,state FROM report_attachments WHERE report_id=$1", [report_id], db=db
            )
        ).rows
        if fresh.get("attachmentsRequired") and not files:
            raise ValueError("Jira requires at least one attachment for this request type.")
        if any(f["state"] != "staged" for f in files):
            raise ValueError("Remove expired or unavailable attachments before approval.")
        await query(
            "UPDATE ticket_reviews SET approved_at=now(),approved_by=$2,approved_payload=$3,updated_at=now() WHERE report_id=$1",
            [report_id, user["id"], payload],
            db=db,
        )
        await enqueue(
            db,
            report,
            user,
            extra={
                **content["candidate"],
                "approvedPayload": payload,
                "approvedSchemaFingerprint": content["form"].get("schemaFingerprint"),
                "approvalVersion": version,
                "attachmentIds": [f["id"] for f in files],
            },
        )
        await query(
            "UPDATE report_attachments SET state='pending',updated_at=now() WHERE report_id=$1",
            [report_id],
            db=db,
        )
        await query(
            "UPDATE reports SET state='submission_pending',summary=$2,updated_at=now() WHERE id=$1",
            [report_id, content["candidate"]["summary"]],
            db=db,
        )
        await query(
            "INSERT INTO operator_events(id,report_id,actor_id,kind,detail) VALUES($1,$2,$3,'requester-approved',$4)",
            [
                str(uuid4()),
                report_id,
                user["id"],
                {"version": version, "attachmentIds": [f["id"] for f in files]},
            ],
            db=db,
        )
        await query(
            "INSERT INTO messages VALUES($1,$2,'assistant',$3,now())",
            [
                str(uuid4()),
                report_id,
                "You approved this ticket. The reviewed fields and selected files are now queued for Jira.",
            ],
            db=db,
        )
    return report_id
