import json
import logging
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from .agents import select_pipeline
from .agents.confidence import has_calibration
from .agents.pricing import pricing_version
from .agents.traces import load_traces
from .attachments import MAX_FILE, attachment_bytes, intake_images
from .auth import assert_origin, authenticate, demo_cookie, validate_session_token
from .config import ROOT, validate_environment
from .db import close_pool, mode, open_pool, query, transaction
from .domain import CorrectionInput, IntakeInput, SessionInput
from .fixtures import catalog
from .connector import ConnectorError
from .intake_prompt import INTAKE_PROMPT_VERSION
from .policy import policy
from .verifier import PROMPT_VERSION as VERIFIER_PROMPT_VERSION


@asynccontextmanager
async def lifespan(app):
    validate_environment(cloud=bool(os.getenv("RAILWAY_ENVIRONMENT_ID")))
    await open_pool()
    yield
    await close_pool()


app = FastAPI(title="Relay IT support", lifespan=lifespan, docs_url=None, redoc_url=None)


def response(value, status=200):
    return JSONResponse(
        jsonable_encoder(value), status_code=status, headers={"Cache-Control": "no-store"}
    )


@app.exception_handler(ValueError)
async def bad_request(req, exc):
    message = (
        "; ".join(e["msg"] for e in exc.errors()) if isinstance(exc, ValidationError) else str(exc)
    )
    status = (
        401
        if message == "Unauthorized"
        else 403
        if message.startswith("Forbidden")
        else 404
        if message == "Not found"
        else 409
        if "draft changed" in message
        or "form changed" in message
        or "Verification changed" in message
        else 400
    )
    return response({"error": message}, status)


@app.exception_handler(ConnectorError)
async def invalid_provider_fields(req, exc):
    return response({"error": str(exc)}, 400)


@app.exception_handler(Exception)
async def request_failed(req, exc):
    logging.getLogger("relay").error("Request failed: %s", type(exc).__name__)
    return response({"error": "Request failed. Please retry."}, 500)


async def read_json(req, limit=15000):
    body = bytearray()
    async for chunk in req.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError("Message too large")
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid JSON request") from exc


MAX_INTAKE_BODY = MAX_FILE + 32 * 1024


async def read_intake(req):
    """The intake JSON and, for a multipart request, the one screenshot sent with it.

    A multipart body must declare its length up front so the file cap holds before parsing.
    """
    if not req.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        return await read_json(req), None
    length = req.headers.get("content-length", "")
    if not length.isdigit() or int(length) > MAX_INTAKE_BODY:
        raise ValueError("Message too large")
    try:
        form = await req.form(max_files=1, max_fields=2)
    except (HTTPException, MultiPartException) as exc:
        raise ValueError("Invalid multipart request") from exc
    payload = form.get("payload")
    if not isinstance(payload, str):
        raise ValueError("Invalid JSON request")
    if len(payload.encode()) > 15000:
        raise ValueError("Message too large")
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise ValueError("Invalid JSON request") from exc
    upload = form.get("image")
    if not isinstance(upload, UploadFile):
        return data, None
    return data, (upload.filename, await upload.read(), upload.content_type)


@app.get("/api/health")
async def health():
    try:
        await query("SELECT 1 FROM reports LIMIT 1")
        await query("SELECT 1 FROM ticket_reviews LIMIT 1")
        return response({"status": "ok"})
    except Exception:
        return response({"status": "unavailable"}, 503)


@app.get("/api/bootstrap")
async def bootstrap(req: Request):
    user = await authenticate(req)
    profiles = (
        (await query("SELECT id,name,role FROM users ORDER BY role,id")).rows
        if mode() == "demo"
        else []
    )
    incidents = (
        await query(
            "SELECT id,title,body,service,updated_at,status FROM sources WHERE kind='incident' AND status='open' AND (visibility='all' OR visibility=$1) AND location=$2 AND updated_at>now()-interval '24 hours'",
            [user["scope"], user["location"]],
        )
    ).rows
    return response(
        {
            "user": user,
            "profiles": profiles,
            "incidents": incidents,
            "catalog": catalog,
            "mode": mode(),
        }
    )


@app.post("/api/session")
async def session(req: Request):
    assert_origin(req)
    data = SessionInput.model_validate(await read_json(req))
    if mode() == "demo":
        if (
            not data.userId
            or not (await query("SELECT 1 FROM users WHERE id=$1", [data.userId])).rowcount
        ):
            raise ValueError("Unknown demo profile")
        value = demo_cookie(data.userId)
    else:
        if not data.token or not await validate_session_token(data.token):
            raise ValueError("Unauthorized")
        value = data.token
    res = response({"ok": True})
    res.set_cookie(
        "relay_session",
        value,
        httponly=True,
        samesite="strict",
        secure=os.getenv("APP_ORIGIN", "").startswith("https:"),
        path="/",
        max_age=8 * 3600,
    )
    return res


@app.post("/api/intake")
async def intake(req: Request):
    from .workflow import intake as save_intake, process_intake

    assert_origin(req)
    user = await authenticate(req)
    raw, image = await read_intake(req)
    data = IntakeInput.model_validate(raw).model_dump(mode="json", exclude_none=True)
    report_id = await save_intake(data, user, image=image)
    if (
        select_pipeline() == "multi"
        and mode() == "live"
        and os.getenv("RELAY_INTAKE_INLINE") != "1"
    ):
        # The multi-agent arm makes several model calls; the worker's `resume_intakes`
        # drives processing and review preparation for every `processing` report.
        return response({"id": report_id, "state": "processing"})
    await process_intake(report_id, user)
    from .review import prepare_review

    await prepare_review(report_id, user)
    return response({"id": report_id})


@app.get("/api/reports")
async def reports(req: Request):
    from .workflow import get_report
    from .privacy import requester_report

    user = await authenticate(req)
    report_id = req.query_params.get("id")
    if report_id:
        try:
            UUID(report_id)
        except ValueError as exc:
            raise ValueError("Invalid report ID") from exc
        report = await requester_report(await get_report(report_id, user), user)
        messages = (
            await query(
                "SELECT * FROM messages WHERE report_id=$1 ORDER BY created_at,id", [report_id]
            )
        ).rows
        images = {image["messageId"]: image for image in await intake_images(report_id)}
        for message in messages:
            image = images.get(message["id"])
            message["image"] = (
                {
                    "id": image["id"],
                    "filename": image["filename"],
                    "contentType": image["contentType"],
                    "size": image["size"],
                    "url": f"/api/attachments?reportId={report_id}&id={image['id']}",
                    "hasContent": image["hasContent"],
                }
                if image
                else None
            )
        ids = (
            report["decision"]["sources"]
            + report["offered"]
            + ([report["related_id"]] if report.get("related_id") else [])
        )
        sources = (
            await query(
                "SELECT id,title,body,kind,service,updated_at,metadata FROM sources WHERE id=ANY($1) AND (visibility='all' OR visibility=$2 OR $3='operator')",
                [ids, user["scope"], user["role"]],
            )
        ).rows
        operations = (
            (
                await query(
                    "SELECT * FROM connector_operations WHERE report_id=$1 ORDER BY created_at",
                    [report_id],
                )
            ).rows
            if user["role"] == "operator"
            else []
        )
        events = (
            (
                await query(
                    "SELECT * FROM operator_events WHERE report_id=$1 ORDER BY created_at DESC",
                    [report_id],
                )
            ).rows
            if user["role"] == "operator"
            else []
        )
        return response(
            dict(
                report=report,
                messages=messages,
                sources=sources,
                operations=operations,
                events=events,
                trace=await load_traces(report_id, user["role"]),
            )
        )
    all_reports = req.query_params.get("all") == "1"
    if all_reports and user["role"] != "operator":
        raise ValueError("Forbidden")
    rows = (
        await query(
            "SELECT * FROM reports WHERE (owner_id=$1 OR $2) AND mode=$3 ORDER BY updated_at DESC LIMIT 100",
            [user["id"], all_reports, mode()],
        )
    ).rows
    return response({"reports": [await requester_report(r, user) for r in rows]})


EVALUATION_PATH = ROOT / "evaluation/arms-results.json"


@app.get("/api/evaluation")
async def evaluation(req: Request):
    """The committed arm comparison, read at request time; per-case rows only on `?cases=1`."""
    user = await authenticate(req)
    if user["role"] != "operator":
        raise ValueError("Forbidden")
    if not EVALUATION_PATH.exists():
        return response({"available": False})
    report = json.loads(EVALUATION_PATH.read_text())
    if req.query_params.get("cases") != "1":
        report.pop("cases", None)
    return response({"available": True, **report})


@app.get("/api/operations")
async def operations(req: Request):
    user = await authenticate(req)
    if user["role"] != "operator":
        raise ValueError("Forbidden")
    health = (
        await query(
            "SELECT last_seen,last_seen>now()-interval '15 seconds' healthy FROM worker_health WHERE id='main'"
        )
    ).rows
    counts = (
        await query(
            "SELECT o.state,count(*)::int count FROM connector_operations o JOIN reports r ON r.id=o.report_id WHERE r.mode=$1 GROUP BY o.state",
            [mode()],
        )
    ).rows
    return response(
        {
            "mode": mode(),
            "worker": health[0] if health else {"healthy": False},
            "counts": counts,
            "jira": "Simulated"
            if mode() == "demo"
            else "Configured"
            if os.getenv("JIRA_API_TOKEN")
            else "Missing credentials",
            "model": "Deterministic demo"
            if mode() == "demo"
            else "Configured"
            if os.getenv("OPENAI_API_KEY")
            else "Missing credentials",
            "security": "Local restricted review only",
            "versions": {
                "policy": policy["version"],
                "scoring": policy["routingScoring"],
                "prompt": INTAKE_PROMPT_VERSION,
                "verifier": VERIFIER_PROMPT_VERSION,
                "pipeline": select_pipeline(),
                "pricing": pricing_version(),
                "calibration": has_calibration(),
            },
        }
    )


@app.post("/api/operations")
async def operate(req: Request):
    from .workflow import get_report, enqueue
    from .connector import connector
    from .jobs import save_ticket

    assert_origin(req)
    user = await authenticate(req)
    if user["role"] != "operator":
        raise ValueError("Forbidden")
    data = CorrectionInput.model_validate(await read_json(req)).model_dump(
        mode="json", exclude_none=True
    )
    report = await get_report(data["reportId"], user)
    if report["mode"] != mode():
        raise ValueError("This report belongs to another application mode.")
    action = data["action"]
    if action == "refresh":
        if not report["provider_key"]:
            raise ValueError("No provider request to refresh")
        await save_ticket(report["id"], await (await connector()).read(report["provider_key"]))
    else:
        async with transaction() as db:
            # Read fresh provider values under the lock before constructing a correction.
            report = (
                await query("SELECT * FROM reports WHERE id=$1 FOR UPDATE", [report["id"]], db=db)
            ).rows[0]
            if action == "acknowledge":
                await query(
                    "UPDATE reports SET acknowledged_at=coalesce(acknowledged_at,now()) WHERE id=$1",
                    [report["id"]],
                    db=db,
                )
            elif action == "correct":
                if (
                    not data.get("team")
                    or not data.get("priority")
                    or not data.get("reason", "").strip()
                ):
                    raise ValueError("Team, priority, and a correction reason are required.")
                if not report["provider_key"]:
                    raise ValueError("Correction requires a created provider request.")
                if (
                    report["decision"]["visibility"] == "restricted"
                    or data["team"] == "Security Review"
                ):
                    raise ValueError(
                        "Restricted destination is unverified. Keep this report in local Security Review."
                    )
                owner = (
                    await query("SELECT * FROM users WHERE id=$1", [report["owner_id"]], db=db)
                ).rows[0]
                await enqueue(
                    db,
                    report,
                    owner,
                    "update",
                    {
                        "team": data["team"],
                        "priority": data["priority"],
                        "expected": {
                            "team": report["provider_team"],
                            "priority": report["provider_priority"],
                        },
                    },
                )
            elif action == "retry":
                await query(
                    "UPDATE connector_operations SET state=CASE WHEN kind='create' AND attempts>0 AND external_key IS NULL THEN 'unknown' ELSE 'pending' END,next_attempt_at=now(),last_error=NULL WHERE report_id=$1 AND state='failed'",
                    [report["id"]],
                    db=db,
                )
            await query(
                "INSERT INTO operator_events(id,report_id,actor_id,kind,detail) VALUES($1,$2,$3,$4,$5)",
                [str(uuid4()), report["id"], user["id"], action, data],
                db=db,
            )
    return response({"ok": True})


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid report or file ID") from exc


def review_input(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] < 1:
        raise ValueError("A valid draft version is required.")
    return identifier(data.get("reportId")), data["version"]


@app.get("/api/review")
async def review_get(req: Request):
    from .review import view_review

    user = await authenticate(req)
    return response({"review": await view_review(identifier(req.query_params.get("id")), user)})


@app.post("/api/review")
async def review_save(req: Request):
    from .review import save_review

    assert_origin(req)
    user = await authenticate(req)
    data = await read_json(req, limit=40000)
    report_id, version = review_input(data)
    return response({"review": await save_review(report_id, user, version, data.get("values"))})


@app.post("/api/review/approve")
async def review_approve(req: Request):
    from .review import approve_review

    assert_origin(req)
    user = await authenticate(req)
    data = await read_json(req)
    report_id, version = review_input(data)
    return response({"id": await approve_review(report_id, user, version)})


@app.post("/api/review/attachments")
async def review_upload(req: Request):
    from .attachments import MAX_FILE, stage_file
    from .review import owner_report

    assert_origin(req)
    user = await authenticate(req)
    report_id = identifier(req.query_params.get("reportId"))
    version = int(req.query_params.get("version", "0"))
    await owner_report(report_id, user)
    content = bytearray()
    async for chunk in req.stream():
        content.extend(chunk)
        if len(content) > MAX_FILE:
            raise ValueError("Each attachment must be no larger than 5 MiB.")
    return response(
        {
            "review": await stage_file(
                report_id, user, version, req.query_params.get("filename"), bytes(content)
            )
        }
    )


@app.get("/api/attachments")
async def attachment(req: Request):
    from .workflow import get_report

    user = await authenticate(req)
    report_id = identifier(req.query_params.get("reportId"))
    file_id = identifier(req.query_params.get("id"))
    await get_report(report_id, user)
    found = await attachment_bytes(report_id, file_id, user)
    if found is None:
        raise ValueError("Not found")
    content_type, content = found
    return Response(
        content,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.delete("/api/review/attachments")
async def review_remove(req: Request):
    from .attachments import remove_file

    assert_origin(req)
    user = await authenticate(req)
    return response(
        {
            "review": await remove_file(
                identifier(req.query_params.get("reportId")),
                user,
                int(req.query_params.get("version", "0")),
                identifier(req.query_params.get("id")),
            )
        }
    )


# API routes take precedence. Only the generated public UI directory is served.
app.mount("/", StaticFiles(directory=ROOT / "out", html=True, check_dir=False), name="frontend")
