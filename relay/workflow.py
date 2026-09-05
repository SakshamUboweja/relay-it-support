"""Durable intake state machine. Model work never holds a database transaction."""

import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .connector import draft
from .db import connection, mode, query, transaction
from .domain import fact
from .intake_evidence import apply_extraction
from .intake_prompt import INTAKE_PROMPT_VERSION
from .model import extract_live, model_settings
from .policy import decide
from .retrieval import retrieve
from .sanitize import sanitize


async def enqueue(db, report, user, kind="create", extra=None):
    payload = {**draft(report, user.get("external_account")), **(extra or {})}
    key = f"create:{report['id']}" if kind == "create" else f"update:{report['id']}:{uuid4()}"
    await query(
        "INSERT INTO connector_operations(id,report_id,operation_key,kind,state,payload,payload_hash,external_key) VALUES($1,$2,$3,$4,'pending',$5,$6,$7) ON CONFLICT(operation_key) DO NOTHING",
        [str(uuid4()), report["id"], key, kind, payload,
         hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
         report.get("provider_key") if kind == "update" else None], db=db,
    )


async def get_report(id, user):
    rows = (await query(
        "SELECT * FROM reports WHERE id=$1 AND (owner_id=$2 OR $3='operator') AND mode=$4",
        [id, user["id"], user["role"], mode()],
    )).rows
    if not rows:
        raise ValueError("Not found")
    return rows[0]


async def intake(raw, user):
    data = {"text": "", "action": "message", **raw}
    text = data["text"].strip()
    if len(text) > 6000 or data["action"] not in {"message", "support", "fixed", "broken", "follow"}:
        raise ValueError("Invalid intake")
    UUID(data["submissionKey"])
    if data.get("reportId"):
        UUID(str(data["reportId"]))
    elif not text:
        raise ValueError("Describe the issue first.")
    text = sanitize(text)
    action = data["action"]
    async with transaction() as db:
        await query("SELECT pg_advisory_xact_lock(hashtext($1))", [str(user["id"]) + data["submissionKey"]], db=db)
        receipt = (await query("SELECT report_id FROM action_receipts WHERE owner_id=$1 AND submission_key=$2", [user["id"], data["submissionKey"]], db=db)).rows
        if receipt:
            existing = (await query("SELECT mode FROM reports WHERE id=$1", [receipt[0]["report_id"]], db=db)).rows
            if not existing or existing[0]["mode"] != mode():
                raise ValueError("This report belongs to another application mode.")
            return receipt[0]["report_id"]
        report = None
        if data.get("reportId"):
            rows = (await query("SELECT * FROM reports WHERE id=$1 AND owner_id=$2 FOR UPDATE", [data["reportId"], user["id"]], db=db)).rows
            if not rows:
                raise ValueError("Not found")
            report = rows[0]
            if report["mode"] != mode():
                raise ValueError("This report belongs to another application mode.")
        elif action not in {"message", "support"}:
            raise ValueError("Start a report first.")
        if report and report["state"] in {"resolved", "created", "submission_pending", "operator_review"} and action != "support":
            raise ValueError("This report already has a saved outcome. Start a new conversation for a new issue.")
        id = report["id"] if report else str(uuid4())
        message_id = str(uuid4())
        if not report:
            report = dict(id=id, owner_id=user["id"], summary=text[:120], state="draft",
                          decision=decide(text, [], user), clarifications=0, offered=[], attempted=[],
                          provider_key=None, provider_url=None, provider_status=None, provider_team=None,
                          provider_priority=None, synced_at=None, acknowledged_at=None, related_id=None, mode=mode())
            await query("INSERT INTO reports(id,owner_id,submission_key,summary,state,decision,mode) VALUES($1,$2,$3,$4,$5,$6,$7)",
                        [id, user["id"], data["submissionKey"], report["summary"], report["state"], report["decision"], mode()], db=db)
        if text and action == "support":
            messages = (await query("SELECT body FROM messages WHERE report_id=$1 AND role='user' ORDER BY created_at", [id], db=db)).rows
            report["decision"] = decide("\n".join([m["body"] for m in messages] + [text]), [], user, report["clarifications"])
            if mode() == "live":
                report["decision"]["supportRequested"] = True
                report["decision"]["facts"].update(device=fact(None), location=fact(None))
            for item in report["decision"]["facts"].values():
                item["evidenceIds"] = [message_id if e == "current-message" else e for e in item["evidenceIds"]]
            await query("UPDATE reports SET decision=$2 WHERE id=$1", [id, report["decision"]], db=db)
            await query("INSERT INTO decisions VALUES($1,$2,$3,now())", [str(uuid4()), id, report["decision"]], db=db)
        if text:
            await query("INSERT INTO messages(id,report_id,role,body) VALUES($1,$2,'user',$3)", [message_id, id, text], db=db)
        if action == "fixed":
            if not report["offered"] or report["state"] != "awaiting_response":
                raise ValueError("No offered procedure is awaiting confirmation.")
            state = "resolved"
            report["attempted"] = list(report["offered"])
            reply = "Glad that helped. Your confirmed resolution is saved. No support ticket was created."
        elif action == "follow":
            related = report["decision"].get("related")
            if not related or report["state"] != "related_suggested":
                raise ValueError("No permitted related incident is available.")
            allowed = await query("SELECT id FROM sources WHERE id=$1 AND status='open' AND (visibility='all' OR visibility=$2) AND location=$3", [related["id"], user["scope"], user["location"]], db=db)
            if not allowed.rowcount:
                raise ValueError("This advisory is no longer available to follow.")
            state = "related_reported"
            report["related_id"] = related["id"]
            reply = "Your individual report is saved and linked to this advisory. Following here does not subscribe you to Jira notifications. You can still send a separate request to support."
        elif action == "support" and text and mode() == "live" and not report["provider_key"] and report["state"] not in {"submission_pending", "operator_review"}:
            state = "processing"
            reply = "Your message is saved. Preparing the details for support…"
        elif action in {"support", "broken"}:
            if action == "broken":
                if not report["offered"] or report["state"] != "awaiting_response":
                    raise ValueError("No offered procedure is awaiting feedback.")
                report["attempted"] = list(report["offered"])
            if not report["provider_key"]:
                state = "operator_review" if report["decision"]["visibility"] == "restricted" else "submission_pending"
                await enqueue(db, report, user)
                reply = ("Your report is saved for restricted Security Review. External delivery is blocked until a restricted destination is configured."
                         if state == "operator_review" else "Your report is saved and queued for support. The request reference will appear when the provider confirms creation.")
            else:
                state = "created"
                reply = f"Your request {report['provider_key']} is already saved."
        else:
            state = "processing"
            reply = "Your message is saved. Checking approved help and service context…"
        await query("UPDATE reports SET state=$2,offered=$3,attempted=$4,related_id=$5,updated_at=now() WHERE id=$1", [id, state, json.dumps(report["offered"]), json.dumps(report["attempted"]), report["related_id"]], db=db)
        await query("INSERT INTO messages(id,report_id,role,body) VALUES($1,$2,'assistant',$3)", [str(uuid4()), id, reply], db=db)
        await query("INSERT INTO action_receipts VALUES($1,$2,$3)", [user["id"], data["submissionKey"], id], db=db)
        return id


async def process_intake(id, user, dependencies=None):
    dependencies = dependencies or {"retrieve": retrieve, "extract_live": extract_live}
    async with connection() as lock:
        acquired = (await query("SELECT pg_try_advisory_lock(hashtext($1)) locked", ["intake:" + str(id)], db=lock)).rows[0]["locked"]
        if not acquired:
            return
        try:
            report = await get_report(id, user)
            if report["state"] != "processing":
                return
            messages = (await query("SELECT id,body FROM messages WHERE report_id=$1 AND role='user' ORDER BY created_at", [id])).rows
            text = "\n".join(m["body"] for m in messages)
            sources, model_error = [], None
            try:
                sources = await dependencies["retrieve"](text, user)
            except Exception:
                model_error = "Model/retrieval unavailable; known facts saved for general intake."
            decision = decide(text, sources, user, report["clarifications"])
            summary = report["summary"]
            requested_support = report["decision"].get("supportRequested", False)
            procedure_tried = False
            if mode() == "live":
                decision["facts"].update(device=fact(None), location=fact(None))
                try:
                    if model_error:
                        raise ValueError(model_error)
                    procedure = decision.get("procedure")
                    result = await dependencies["extract_live"](text, [str(m["id"]) for m in messages], {"id": procedure["id"], "body": procedure["body"]} if procedure else None)
                    decision.update(model=os.environ["OPENAI_MODEL"], usage=result["usage"], promptVersion=INTAKE_PROMPT_VERSION, reasoningEffort=model_settings()["effort"])
                    apply_extraction(decision, result["data"])
                    summary = result["data"]["summary"]
                    requested_support = requested_support or bool(result["data"].get("supportRequestQuote"))
                    procedure_tried = bool(result["data"].get("procedureAttemptedQuote"))
                except Exception:
                    model_error = "Live model failed. Report saved for human intake."
                    decision["model"] = "live-failed"
                    if decision["visibility"] != "restricted":
                        decision.update(team="Service Desk", accepted=False)
                    decision.update(procedure=None, question=None)
                    decision["reasons"].append("model-unavailable")
            for item in decision["facts"].values():
                item["evidenceIds"] = [evidence for e in item["evidenceIds"] for evidence in ([str(m["id"]) for m in messages] if e == "current-message" else [e])]
            state, reply = "submission_pending", "Your report is saved and queued for support."
            clarification = report["clarifications"]
            offered = list(report["offered"])
            if decision["visibility"] == "restricted":
                state = "operator_review"
                reply = "This may be a security concern. I’ve saved it for restricted Security Review and stopped routine troubleshooting. External handoff awaits a verified restricted destination."
            elif requested_support or procedure_tried:
                reply = ("I’ve included the troubleshooting you already tried and queued your report for support."
                         if procedure_tried or decision["facts"].get("attemptedSteps", {}).get("value") else "As requested, I’ve queued your report for support.")
            elif decision.get("question") and clarification < 1:
                state = "awaiting_clarification"
                clarification += 1
                reply = decision["question"] + " You can also send the details you have directly to support."
            elif decision.get("related"):
                state = "related_suggested"
                reply = f"There’s an active advisory for {decision['related']['title']}. You can follow this advisory here or send a separate request to support. Your individual report is saved either way."
            elif decision.get("procedure") and not offered and report["clarifications"] == 0:
                state = "awaiting_response"
                offered.append(decision["procedure"]["id"])
                reply = decision["procedure"]["body"]
            if model_error:
                reply = model_error + " Your original message is preserved."
            async with transaction() as db:
                current = (await query("SELECT * FROM reports WHERE id=$1 FOR UPDATE", [id], db=db)).rows[0]
                if current["state"] != "processing":
                    return
                await query("UPDATE reports SET state=$2,decision=$3,clarifications=$4,offered=$5,summary=$6,updated_at=now() WHERE id=$1", [id, state, decision, clarification, json.dumps(offered), summary], db=db)
                await query("INSERT INTO decisions VALUES($1,$2,$3,now())", [str(uuid4()), id, decision], db=db)
                await query("INSERT INTO context_snapshots VALUES($1,$2,$3,now())", [str(uuid4()), id, {"simulated": mode() == "demo", "directory": user if mode() == "demo" else None, "retrievedSourceIds": [s["id"] for s in sources[:5]], "capturedAt": datetime.now(timezone.utc).isoformat()}], db=db)
                await query("INSERT INTO messages VALUES($1,$2,'assistant',$3,now())", [str(uuid4()), id, reply], db=db)
                if state in {"submission_pending", "operator_review"}:
                    await enqueue(db, {**current, "summary": summary, "state": state, "decision": decision, "offered": offered}, user)
        finally:
            await query("SELECT pg_advisory_unlock(hashtext($1))", ["intake:" + str(id)], db=lock)
