"""Jira adapter. Durable retry and reconciliation decisions belong to the worker."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .config import ROOT
from .db import mode, query
from .ticket_description import ticket_description

TEAMS = ("Service Desk", "Identity & Access", "Network", "Endpoint", "Business Applications", "Security Review")
Draft = dict[str, Any]
Ticket = dict[str, Any]


class ConnectorError(Exception):
    def __init__(self, message: str, ambiguous: bool = False, retry_after: float = 0,
                 status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous
        self.retry_after = retry_after
        self.status = status
        self.retryable = retryable


class JiraConfig:
    @staticmethod
    def parse(data: Any) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Jira configuration must be an object")
        patterns = {
            "site": r"https://[^/?#:@]+\.atlassian\.net/?",
            "projectKey": r"[A-Z][A-Z0-9_]+",
            "serviceDeskId": r"\d+", "requestTypeId": r"\d+", "generalRequestTypeId": r"\d+",
            "supportTeamFieldId": r"customfield_\d+",
        }
        for key, pattern in patterns.items():
            if not isinstance(data.get(key), str) or not re.fullmatch(pattern, data[key]):
                raise ValueError(f"Invalid Jira configuration field: {key}")
        options = data.get("teamOptions")
        if not isinstance(options, dict) or set(options) != set(TEAMS) or any(
            not isinstance(value, str) or not value for value in options.values()
        ):
            raise ValueError("Jira teamOptions must map every supported team")
        priorities = data.get("priorityIds")
        if not isinstance(priorities, dict) or any(
            not isinstance(priorities.get(key), str) for key in ("normal", "elevated", "urgent")
        ):
            raise ValueError("Jira priorityIds must map normal, elevated and urgent")
        if data.get("reporterMode") not in ("integration-account", "on-behalf-of"):
            raise ValueError("Invalid Jira reporterMode")
        if data.get("securityEnforced", False) is not False:
            raise ValueError("Restricted Jira destinations are not verified")
        if not isinstance(data.get("defaults", {}), dict):
            raise ValueError("Jira defaults must be an object")
        return {**data, "defaults": data.get("defaults", {}), "securityEnforced": False}


class DemoConnector:
    async def discover(self):
        return {"mode": "demo", "provider": "Simulated Jira", "requiredFields": ["summary", "description"], "restricted": False}

    async def validate(self, d: Draft):
        if not d.get("summary") or not d.get("description"):
            raise ConnectorError("Summary and description are required.")
        if d["restricted"]:
            raise ConnectorError("Restricted external destination is not configured. Saved for operator review.")

    @staticmethod
    def ticket(row: dict) -> Ticket:
        return {"key": row["key"], "url": None, "status": row["status"], "team": row["team"], "priority": row["priority"]}

    async def create(self, d: Draft) -> Ticket:
        await self.validate(d)
        n = (await query("SELECT nextval('demo_ticket_seq') n")).rows[0]["n"]
        result = await query(
            "INSERT INTO demo_tickets(key,marker,summary,team,priority) VALUES($1,$2,$3,$4,$5) "
            "ON CONFLICT(marker) DO UPDATE SET marker=EXCLUDED.marker RETURNING *",
            [f"DEMO-{n}", d["marker"], d["summary"], d["team"], d["priority"]],
        )
        if os.getenv("DEMO_LOST_CREATE_RESPONSE") == "1":
            raise ConnectorError("Simulated connection lost after provider accepted creation.", ambiguous=True)
        return self.ticket(result.rows[0])

    async def read(self, key: str) -> Ticket:
        rows = (await query("SELECT * FROM demo_tickets WHERE key=$1", [key])).rows
        if not rows:
            raise ConnectorError("Demo request not found")
        return self.ticket(rows[0])

    async def update(self, key: str, team: str, priority: str, expected: dict | None = None) -> Ticket:
        old = await self.read(key)
        if expected and any(old[k] != expected[k] for k in ("team", "priority")) and not (
            old["team"] == team and old["priority"] == priority
        ):
            raise ConnectorError("Provider values changed. Review the human update before correcting.")
        if os.getenv("DEMO_FAIL_UPDATE") == "1":
            raise ConnectorError("Simulated routing update failure.")
        rows = (await query("UPDATE demo_tickets SET team=$2,priority=$3 WHERE key=$1 RETURNING *", [key, team, priority])).rows
        return self.ticket(rows[0])

    async def reconcile(self, marker: str) -> list[Ticket]:
        return [self.ticket(row) for row in (await query("SELECT * FROM demo_tickets WHERE marker=$1", [marker])).rows]


class JiraConnector:
    def __init__(self, config: dict, transport: httpx.AsyncBaseTransport | None = None,
                 client: httpx.AsyncClient | None = None):
        self.config = JiraConfig.parse(config)
        self.transport = transport
        self.client = client

    async def request(self, path: str, method: str = "GET", body: Any = None, create: bool = False):
        email, token = os.getenv("JIRA_EMAIL"), os.getenv("JIRA_API_TOKEN")
        if not email or not token:
            raise ConnectorError("JIRA_EMAIL and JIRA_API_TOKEN are missing.")
        kwargs = {"method": method, "url": self.config["site"].rstrip("/") + path,
                  "auth": httpx.BasicAuth(email, token), "headers": {"Accept": "application/json", "Content-Type": "application/json"},
                  "timeout": 20, "follow_redirects": False}
        if body is not None:
            kwargs["json"] = body
        try:
            if self.client is not None:
                response = await self.client.request(**kwargs)
            else:
                async with httpx.AsyncClient(transport=self.transport) as client:
                    response = await client.request(**kwargs)
        except httpx.RequestError:
            raise ConnectorError("Jira network failure or timeout.", ambiguous=create, retryable=True) from None
        if not response.is_success:
            retry_after = 0.0
            retry = response.headers.get("retry-after")
            if retry:
                try:
                    retry_after = max(0, float(retry))
                except ValueError:
                    try:
                        date = parsedate_to_datetime(retry)
                        if date.tzinfo is None:
                            date = date.replace(tzinfo=timezone.utc)
                        retry_after = max(0, (date - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
            status = response.status_code
            message = ("Jira authentication or permissions need operator attention." if status in (401, 403)
                       else f"Jira {status}: " + ("Invalid provider fields; run discovery and check mappings." if status == 400 else "Provider request failed."))
            raise ConnectorError(message, ambiguous=create and status >= 500, retry_after=retry_after, status=status)
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError:
            raise ConnectorError("Jira returned an invalid response.", ambiguous=create, retryable=not create) from None

    async def fields(self, type_id: str):
        return await self.request(f'/rest/servicedeskapi/servicedesk/{self.config["serviceDeskId"]}/requesttype/{type_id}/field')

    async def discover(self):
        normal = await self.fields(self.config["requestTypeId"])
        general = await self.fields(self.config["generalRequestTypeId"])
        def missing(fields):
            return [x["fieldId"] for x in fields.get("requestTypeFields", []) if x.get("required")
                    and x["fieldId"] not in ("summary", "description") and x["fieldId"] not in self.config["defaults"]]
        return {"normal": normal, "general": general, "missingNormal": missing(normal), "missingGeneral": missing(general),
                "reporterMode": self.config["reporterMode"], "security": "blocked: no verified restricted destination"}

    async def payload(self, d: Draft):
        if d["restricted"]:
            raise ConnectorError("Security destination permissions are unverified. Restricted report stays local.")
        type_id = self.config["requestTypeId"]
        fields = await self.fields(type_id)
        values = {**self.config["defaults"], "summary": d["summary"], "description": f'{d["description"]}\n\nIntake correlation: {d["marker"]}'}
        def missing(fields):
            return [x for x in fields.get("requestTypeFields", []) if x.get("required") and values.get(x["fieldId"]) in (None, "")]
        if missing(fields):
            type_id = self.config["generalRequestTypeId"]
            fields = await self.fields(type_id)
        if missing(fields):
            raise ConnectorError("Required Jira fields have no truthful value/default: " + ", ".join(x["fieldId"] for x in missing(fields)))
        for field in fields.get("requestTypeFields", []):
            if field["fieldId"] in values and field.get("validValues"):
                value = values[field["fieldId"]]
                value_id = value.get("id", value.get("value")) if isinstance(value, dict) else value
                if not any(str(v.get("value")) == str(value_id) for v in field["validValues"]):
                    raise ConnectorError(f'Invalid configured value for {field["fieldId"]}')
        body = {"serviceDeskId": self.config["serviceDeskId"], "requestTypeId": type_id,
                "isAdfRequest": False, "requestFieldValues": values}
        if self.config["reporterMode"] == "on-behalf-of":
            if not fields.get("canRaiseOnBehalfOf") or not d.get("externalAccount"):
                raise ConnectorError("Raising on behalf requires provider permission and employee account mapping.")
            body["raiseOnBehalfOf"] = d["externalAccount"]
        return body

    async def validate(self, d: Draft):
        await self.payload(d)

    async def create(self, d: Draft) -> Ticket:
        result = await self.request("/rest/servicedeskapi/request", "POST", await self.payload(d), True)
        if not result.get("issueKey"):
            raise ConnectorError("Jira create response omitted its key.", ambiguous=True)
        try:
            return await self.read(result["issueKey"])
        except Exception:
            raise ConnectorError("Request created but its initial routing snapshot could not be read. Reconcile before continuing.", ambiguous=True) from None

    def safe_url(self, url: Any) -> str | None:
        if not isinstance(url, str):
            return None
        try:
            resolved = urljoin(self.config["site"], url)
            candidate, site = urlsplit(resolved), urlsplit(self.config["site"])
            if (candidate.scheme, candidate.hostname, candidate.port or 443) == (site.scheme, site.hostname, site.port or 443):
                return resolved
        except ValueError:
            pass
        return None

    async def read(self, key: str) -> Ticket:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key):
            raise ConnectorError("Invalid Jira key")
        result = await self.request(f"/rest/servicedeskapi/request/{key}")
        fields = (await self.request(f'/rest/api/3/issue/{key}?fields={self.config["supportTeamFieldId"]},priority')).get("fields") or {}
        team_id = (fields.get(self.config["supportTeamFieldId"]) or {}).get("id")
        priority_id = (fields.get("priority") or {}).get("id")
        return {"key": result["issueKey"], "url": self.safe_url((result.get("_links") or {}).get("web")),
                "status": (result.get("currentStatus") or {}).get("status") or "Unknown",
                "team": next((k for k, v in self.config["teamOptions"].items() if v == team_id), team_id),
                "priority": next((k for k, v in self.config["priorityIds"].items() if v == priority_id), priority_id)}

    async def update(self, key: str, team: str, priority: str, expected: dict | None = None) -> Ticket:
        current = await self.read(key)
        if current["team"] == team and current["priority"] == priority:
            return current
        if expected and any(current[k] != expected[k] for k in ("team", "priority")):
            raise ConnectorError("Provider routing changed since this correction. Review before retrying.")
        meta = await self.request(f"/rest/api/3/issue/{key}/editmeta")
        for field, value in ((self.config["supportTeamFieldId"], self.config["teamOptions"][team]), ("priority", self.config["priorityIds"][priority])):
            metadata = (meta.get("fields") or {}).get(field)
            if not metadata or "set" not in (metadata.get("operations") or []) or not any(v.get("id") == value for v in (metadata.get("allowedValues") or [])):
                raise ConnectorError(f"Field {field} or configured option is not writable.")
        await self.request(f"/rest/api/3/issue/{key}", "PUT", {"fields": {
            self.config["supportTeamFieldId"]: {"id": self.config["teamOptions"][team]}, "priority": {"id": self.config["priorityIds"][priority]}}})
        actual = await self.read(key)
        if actual["team"] != team or actual["priority"] != priority:
            raise ConnectorError("Provider has not confirmed the requested routing values.")
        return actual

    async def reconcile(self, marker: str) -> list[Ticket]:
        if not re.fullmatch(r"relay[a-f0-9]{32}", marker):
            raise ConnectorError("Invalid correlation marker")
        token = None
        matches = []
        for _ in range(5):
            body = {"jql": f'project = "{self.config["projectKey"]}" AND description ~ "{marker}"', "fields": ["description"], "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            result = await self.request("/rest/api/3/search/jql", "POST", body)
            for issue in result.get("issues", []):
                if issue["key"].startswith(self.config["projectKey"] + "-") and f"Intake correlation: {marker}" in json.dumps((issue.get("fields") or {}).get("description")):
                    matches.append(await self.read(issue["key"]))
            token = result.get("nextPageToken")
            if not token:
                return matches
        raise ConnectorError("Correlation search exceeded bounded pagination. Operator review required.")


async def connector() -> DemoConnector | JiraConnector:
    if mode() == "demo":
        return DemoConnector()
    try:
        raw = os.getenv("JIRA_CONFIG_JSON") or (ROOT / "config" / "jira.json").read_text()
        data = json.loads(raw)
    except (OSError, ValueError):
        raise ConnectorError("Set valid JIRA_CONFIG_JSON or create config/jira.json from config/jira.example.json.") from None
    return JiraConnector(JiraConfig.parse(data))


def draft(report: dict, external_account: str | None) -> Draft:
    return {"summary": report["summary"], "description": ticket_description(report), "team": report["decision"]["team"],
            "priority": report["decision"]["priority"], "restricted": report["decision"]["visibility"] == "restricted",
            "externalAccount": external_account, "marker": "relay" + report["id"].replace("-", "")}
