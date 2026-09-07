"""Small, durable attachments staged in PostgreSQL until requester approval."""

import re
from pathlib import PurePath
from uuid import uuid4

from .connector import ConnectorError, connector
from .db import connection, mode, query, transaction
from .review import editable, owner_report, row_for, view_review

MAX_FILE = 5 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg"}
TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".log": "text/plain",
}


def validate_file(filename, content):
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 160
        or filename != PurePath(filename).name
        or re.search(r"[\x00-\x1f\x7f/\\]", filename)
    ):
        raise ValueError("Use a simple filename of at most 160 characters.")
    suffix = PurePath(filename).suffix.lower()
    mime = TYPES.get(suffix)
    if not mime or not 0 < len(content) <= MAX_FILE:
        raise ValueError("Attach PNG, JPEG, PDF, TXT or LOG files, up to 5 MiB each.")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("This file does not match its PNG extension.")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("This file does not match its JPEG extension.")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("This file does not match its PDF extension.")
    if mime == "text/plain":
        try:
            text = content.decode("utf-8")
            if "\x00" in text:
                raise ValueError
        except (UnicodeError, ValueError):
            raise ValueError(
                "Text attachments must contain UTF-8 text without null bytes."
            ) from None
    return mime


async def reserve_slot(db, report_id, user, size):
    """Hold the quota lock for the caller's transaction and check one more file fits.

    Callers lock the report row first, so the order is always row, then quota lock.
    """
    # Bound total local storage and serialize quota checks across reports.
    await query("SELECT pg_advisory_xact_lock(726351903)", db=db)
    quota = (
        await query(
            "SELECT count(*) FILTER (WHERE a.report_id=$1) files,coalesce(sum(a.size) FILTER (WHERE r.owner_id=$2 AND a.content IS NOT NULL),0) owner_bytes,coalesce(sum(a.size) FILTER (WHERE a.content IS NOT NULL),0) total_bytes FROM report_attachments a JOIN reports r ON r.id=a.report_id",
            [report_id, user["id"]],
            db=db,
        )
    ).rows[0]
    if quota["files"] >= 3:
        raise ValueError("You can attach up to three files per ticket.")
    if (
        quota["owner_bytes"] + size > 50 * 1024 * 1024
        or quota["total_bytes"] + size > 500 * 1024 * 1024
    ):
        raise ValueError("Attachment storage is full. Remove unused draft files before uploading.")


async def stage_intake_image(db, report_id, user, message_id, filename, content):
    """Stage the screenshot sent with a chat message, inside the caller's intake transaction.

    No review row or version exists yet; the file waits locally like any staged upload.
    """
    mime = validate_file(filename, content)
    if mime not in IMAGE_TYPES:
        raise ValueError("Attach a PNG or JPEG screenshot with your message.")
    await reserve_slot(db, report_id, user, len(content))
    file_id = str(uuid4())
    provider_name = file_id + "-" + filename
    await query(
        "INSERT INTO report_attachments(id,report_id,filename,provider_filename,content_type,size,content,state,origin,message_id) VALUES($1,$2,$3,$4,$5,$6,$7,'staged','intake_image',$8)",
        [file_id, report_id, filename, provider_name, mime, len(content), content, message_id],
        db=db,
    )
    return {
        "id": file_id,
        "filename": filename,
        "content_type": mime,
        "size": len(content),
        "provider_filename": provider_name,
    }


async def attachment_bytes(report_id, attachment_id, user):
    """The stored file for its owner or an operator, while the local copy still exists:
    `(content_type, content, filename)`, or None."""
    rows = (
        await query(
            "SELECT a.content_type,a.content,a.filename FROM report_attachments a JOIN reports r ON r.id=a.report_id WHERE a.id=$1 AND a.report_id=$2 AND (r.owner_id=$3 OR $4='operator') AND r.mode=$5 AND a.content IS NOT NULL",
            [attachment_id, report_id, user["id"], user["role"], mode()],
        )
    ).rows
    if not rows:
        return None
    return rows[0]["content_type"], bytes(rows[0]["content"]), rows[0]["filename"]


async def intake_images(report_id):
    """Screenshots sent with chat messages, oldest first, with whether bytes are still local."""
    rows = (
        await query(
            "SELECT id,filename,content_type,size,message_id,content IS NOT NULL has_content FROM report_attachments WHERE report_id=$1 AND origin='intake_image' ORDER BY created_at,id",
            [report_id],
        )
    ).rows
    return [
        {
            "id": r["id"],
            "filename": r["filename"],
            "contentType": r["content_type"],
            "size": r["size"],
            "messageId": r["message_id"],
            "hasContent": r["has_content"],
        }
        for r in rows
    ]


async def stage_file(report_id, user, version, filename, content):
    mime = validate_file(filename, content)
    async with transaction() as db:
        report = await owner_report(report_id, user, db=db, lock=True)
        row = await row_for(report_id, db=db)
        editable(row, report, version)
        if not row["content"]["form"].get("attachmentsAllowed"):
            raise ValueError("This Jira request type does not accept attachments.")
        await reserve_slot(db, report_id, user, len(content))
        file_id = str(uuid4())
        provider_name = file_id + "-" + filename
        await query(
            "INSERT INTO report_attachments(id,report_id,filename,provider_filename,content_type,size,content) VALUES($1,$2,$3,$4,$5,$6,$7)",
            [file_id, report_id, filename, provider_name, mime, len(content), content],
            db=db,
        )
        await query(
            "UPDATE ticket_reviews SET version=version+1,updated_at=now() WHERE report_id=$1",
            [report_id],
            db=db,
        )
    return await view_review(report_id, user)


async def remove_file(report_id, user, version, file_id):
    async with transaction() as db:
        report = await owner_report(report_id, user, db=db, lock=True)
        row = await row_for(report_id, db=db)
        editable(row, report, version)
        deleted = await query(
            "DELETE FROM report_attachments WHERE id=$1 AND report_id=$2",
            [file_id, report_id],
            db=db,
        )
        if not deleted.rowcount:
            raise ValueError("Not found")
        await query(
            "UPDATE ticket_reviews SET version=version+1,updated_at=now() WHERE report_id=$1",
            [report_id],
            db=db,
        )
    return await view_review(report_id, user)


async def process_attachments(provider=None, stop=None):
    rows = (
        await query(
            "SELECT a.id FROM report_attachments a JOIN reports r ON r.id=a.report_id JOIN ticket_reviews t ON t.report_id=r.id WHERE a.state IN ('pending','unknown') AND a.next_attempt_at<=now() AND r.mode=$1 AND r.provider_key IS NOT NULL AND t.approved_at IS NOT NULL ORDER BY a.created_at LIMIT 20",
            [mode()],
        )
    ).rows
    if not rows:
        return
    provider = provider or await connector()
    for entry in rows:
        if stop is not None and stop.is_set():
            return
        async with connection() as lock:
            acquired = (
                await query(
                    "SELECT pg_try_advisory_lock(hashtext($1)) locked",
                    ["attachment:" + entry["id"]],
                    db=lock,
                )
            ).rows[0]["locked"]
            if not acquired:
                continue
            try:
                await deliver_file(entry["id"], provider)
            finally:
                await query(
                    "SELECT pg_advisory_unlock(hashtext($1))",
                    ["attachment:" + entry["id"]],
                    db=lock,
                )


async def deliver_file(file_id, provider):
    row = (
        await query(
            "SELECT a.*,r.provider_key FROM report_attachments a JOIN reports r ON r.id=a.report_id JOIN ticket_reviews t ON t.report_id=r.id WHERE a.id=$1 AND r.mode=$2 AND t.approved_at IS NOT NULL AND r.provider_key IS NOT NULL AND a.state IN ('pending','unknown') AND a.next_attempt_at<=now()",
            [file_id, mode()],
        )
    ).rows
    if not row:
        return
    f = row[0]
    crossed_boundary = False
    try:
        if f["state"] == "unknown":
            found = await provider.attachment_exists(
                f["provider_key"], f["provider_filename"], f["size"]
            )
            if not found:
                await query(
                    "UPDATE report_attachments SET state=$2,attempts=attempts+1,last_error=$3,next_attempt_at=now()+interval '30 seconds',updated_at=now() WHERE id=$1",
                    [
                        file_id,
                        "failed" if f["attempts"] >= 5 else "unknown",
                        "Upload confirmation is uncertain. Jira will be checked; the file will not be uploaded again automatically.",
                    ],
                )
                return
        else:
            if f["content"] is None:
                raise ValueError("Attachment contents are no longer available.")
            await query(
                "UPDATE report_attachments SET state='unknown',attempts=attempts+1,updated_at=now() WHERE id=$1",
                [file_id],
            )
            crossed_boundary = True
            await provider.attach(
                f["provider_key"], f["provider_filename"], bytes(f["content"]), f["content_type"]
            )
        # Retain metadata; clear the local file copy after Jira confirms acceptance.
        await query(
            "UPDATE report_attachments SET state='succeeded',content=NULL,last_error=NULL,updated_at=now() WHERE id=$1",
            [file_id],
        )
    except Exception as error:
        definite = (
            isinstance(error, ConnectorError) and 400 <= error.status < 500 and not error.ambiguous
        )
        if (crossed_boundary and not definite) or f["state"] == "unknown":
            state = "unknown" if f["attempts"] < 5 else "failed"
        elif isinstance(error, ConnectorError) and error.status == 429 and f["attempts"] < 3:
            state = "pending"
        else:
            state = "failed"
        await query(
            "UPDATE report_attachments SET state=$2,last_error=$3,next_attempt_at=now()+interval '30 seconds',updated_at=now() WHERE id=$1",
            [file_id, state, str(error)],
        )


async def expire_files():
    await query(
        "UPDATE report_attachments a SET content=NULL,state='expired',last_error='Local file expired after seven days. Remove it and attach it again before approval.',updated_at=now() FROM reports r WHERE r.id=a.report_id AND r.mode=$1 AND a.content IS NOT NULL AND a.state IN ('staged','failed') AND a.created_at<now()-interval '7 days'",
        [mode()],
    )
