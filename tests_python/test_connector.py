"""Offline contract tests for the Jira adapter; never create real tickets."""
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from relay.connector import ConnectorError, JiraConfig, JiraConnector, connector

MAPPING = JiraConfig.parse({
    "site": "https://test.atlassian.net", "projectKey": "HELP", "serviceDeskId": "1",
    "requestTypeId": "2", "generalRequestTypeId": "3", "supportTeamFieldId": "customfield_10001",
    "teamOptions": {"Service Desk": "10", "Identity & Access": "11", "Network": "12", "Endpoint": "13",
                    "Business Applications": "14", "Security Review": "15"},
    "priorityIds": {"normal": "3", "elevated": "2", "urgent": "1"}, "reporterMode": "integration-account",
})
DRAFT = {"summary": "VPN cannot connect", "description": "Impact unknown. No steps attempted.", "team": "Network",
         "priority": "normal", "restricted": False, "externalAccount": None, "marker": "relay" + "a" * 32}
FIELDS = {"requestTypeFields": [{"fieldId": "summary", "required": True}, {"fieldId": "description", "required": True}],
          "canRaiseOnBehalfOf": False}


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "test@example.invalid")
    monkeypatch.setenv("JIRA_API_TOKEN", "stub-credential")


def api(handler, mapping=None):
    return JiraConnector(mapping or MAPPING, httpx.MockTransport(handler))


def ticket_response(request, team="10"):
    if "/rest/api/3/issue/" in request.url.path:
        return httpx.Response(200, json={"fields": {"customfield_10001": {"id": team}, "priority": {"id": "3"}}})
    return httpx.Response(200, json={"issueKey": "HELP-1", "currentStatus": {"status": "Open"},
                                    "_links": {"web": "https://test.atlassian.net/HELP-1"}})


@pytest.mark.asyncio
async def test_network_retry_and_create_ambiguity():
    def fail(request):
        raise httpx.ConnectError("reset", request=request)
    client = api(fail)
    for create in (False, True):
        with pytest.raises(ConnectorError) as caught:
            await client.request("/test", "POST" if create else "GET", {}, create)
        assert caught.value.retryable
        assert caught.value.ambiguous == create


@pytest.mark.asyncio
async def test_create_uses_jsm_and_reads_actual_routing():
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=FIELDS)
        if request.method == "POST":
            return httpx.Response(201, json={"issueKey": "HELP-1"})
        return ticket_response(request)
    result = await api(handle).create(DRAFT)
    assert result["team"] == "Service Desk"
    assert result["priority"] == "normal"
    posts = [r for r in calls if r.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url.path == "/rest/servicedeskapi/request"
    body = json.loads(posts[0].content)
    assert body["serviceDeskId"] == "1" and body["requestTypeId"] == "2"
    assert DRAFT["marker"] in body["requestFieldValues"]["description"]
    assert "raiseOnBehalfOf" not in body


@pytest.mark.asyncio
async def test_missing_fields_cannot_be_invented():
    client = api(lambda _: httpx.Response(200, json={"requestTypeFields": [{"fieldId": "asset", "required": True}]}))
    with pytest.raises(ConnectorError, match="no truthful value"):
        await client.validate(DRAFT)


@pytest.mark.asyncio
async def test_missing_normal_field_falls_back_to_general():
    client = api(lambda r: httpx.Response(200, json={"requestTypeFields": [{"fieldId": "asset", "required": True}]}
                                         if "/requesttype/2/" in r.url.path else FIELDS))
    assert (await client.payload(DRAFT))["requestTypeId"] == "3"


@pytest.mark.asyncio
async def test_defaults_are_checked_against_provider_options():
    client = api(lambda _: httpx.Response(200, json={"requestTypeFields": [{"fieldId": "asset", "required": True,
                       "validValues": [{"value": "allowed"}]}]}), {**MAPPING, "defaults": {"asset": {"id": "wrong"}}})
    with pytest.raises(ConnectorError, match="Invalid configured value"):
        await client.validate(DRAFT)


@pytest.mark.asyncio
async def test_on_behalf_requires_permission_and_employee_mapping():
    client = api(lambda _: httpx.Response(200, json=FIELDS), {**MAPPING, "reporterMode": "on-behalf-of"})
    with pytest.raises(ConnectorError, match="permission"):
        await client.validate({**DRAFT, "externalAccount": "employee"})


@pytest.mark.asyncio
async def test_restricted_never_makes_http_request():
    def fail(_):
        pytest.fail("Restricted report reached transport")
    with pytest.raises(ConnectorError, match="Security destination"):
        await api(fail).create({**DRAFT, "restricted": True})


@pytest.mark.asyncio
async def test_timeout_create_is_not_automatically_retried():
    posts = []
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=FIELDS)
        posts.append(request)
        raise httpx.ReadTimeout("timeout", request=request)
    with pytest.raises(ConnectorError) as caught:
        await api(handle).create(DRAFT)
    assert caught.value.ambiguous
    assert len(posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,ambiguous", [(400, False), (401, False), (403, False), (429, False), (500, True), (503, True)])
async def test_http_failure_classification(status, ambiguous):
    client = api(lambda _: httpx.Response(status, json={}, headers={"Retry-After": "45"}))
    with pytest.raises(ConnectorError) as caught:
        await client.request("/create", "POST", {}, True)
    assert caught.value.status == status
    assert caught.value.retry_after == 45
    assert caught.value.ambiguous == ambiguous


@pytest.mark.asyncio
async def test_retry_after_http_date():
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90), usegmt=True)
    with pytest.raises(ConnectorError) as caught:
        await api(lambda _: httpx.Response(429, headers={"Retry-After": date})).discover()
    assert 88 <= caught.value.retry_after <= 90


@pytest.mark.asyncio
async def test_routing_checks_options_and_confirms_readback():
    updated = False
    put = None
    def handle(request):
        nonlocal updated, put
        if request.url.path.endswith("/editmeta"):
            return httpx.Response(200, json={"fields": {"customfield_10001": {"operations": ["set"], "allowedValues": [{"id": "12"}]},
                                                   "priority": {"operations": ["set"], "allowedValues": [{"id": "3"}]}}})
        if request.method == "PUT":
            put = json.loads(request.content)
            updated = True
            return httpx.Response(204)
        return ticket_response(request, "12" if updated else "10")
    result = await api(handle).update("HELP-1", "Network", "normal", {"team": "Service Desk", "priority": "normal"})
    assert result["team"] == "Network"
    assert put == {"fields": {"customfield_10001": {"id": "12"}, "priority": {"id": "3"}}}


@pytest.mark.asyncio
async def test_human_routing_change_is_not_overwritten():
    requests = []
    def handle(request):
        requests.append(request)
        return ticket_response(request, "13")
    with pytest.raises(ConnectorError, match="changed since"):
        await api(handle).update("HELP-1", "Network", "normal", {"team": "Service Desk", "priority": "normal"})
    assert all(r.method == "GET" for r in requests)


@pytest.mark.asyncio
async def test_already_applied_update_is_idempotent_despite_old_snapshot():
    result = await api(lambda r: ticket_response(r, "12")).update("HELP-1", "Network", "normal", {"team": "Service Desk", "priority": "normal"})
    assert result["team"] == "Network"


@pytest.mark.asyncio
async def test_reconciliation_filters_approximate_and_wrong_project():
    def handle(request):
        assert request.url.path.endswith("/search/jql")
        return httpx.Response(200, json={"issues": [
            {"key": "OTHER-1", "fields": {"description": "Intake correlation: " + DRAFT["marker"]}},
            {"key": "HELP-2", "fields": {"description": "similar marker"}}]})
    assert await api(handle).reconcile(DRAFT["marker"]) == []


@pytest.mark.asyncio
async def test_reconciliation_paginates_and_reads_exact_match():
    bodies = []
    def handle(request):
        if request.url.path.endswith("/search/jql"):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"issues": [], "nextPageToken": "page2"} if len(bodies) == 1 else {
                "issues": [{"key": "HELP-1", "fields": {"description": {"text": "Intake correlation: " + DRAFT["marker"]}}}]})
        return ticket_response(request)
    result = await api(handle).reconcile(DRAFT["marker"])
    assert bodies[1]["nextPageToken"] == "page2"
    assert result[0]["key"] == "HELP-1"


@pytest.mark.asyncio
async def test_reconciliation_is_bounded():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"issues": [], "nextPageToken": "more"})
    with pytest.raises(ConnectorError, match="bounded pagination"):
        await api(handle).reconcile(DRAFT["marker"])
    assert len(requests) == 5


@pytest.mark.asyncio
async def test_lost_create_readback_requires_reconciliation():
    def handle(request):
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=FIELDS)
        if request.method == "POST":
            return httpx.Response(201, json={"issueKey": "HELP-1"})
        return httpx.Response(503, json={})
    with pytest.raises(ConnectorError, match="Reconcile") as caught:
        await api(handle).create(DRAFT)
    assert caught.value.ambiguous


def test_url_boundary():
    client = JiraConnector(MAPPING)
    assert client.safe_url("/HELP-1") == "https://test.atlassian.net/HELP-1"
    assert client.safe_url("https://evil.invalid/HELP-1") is None
    assert client.safe_url("//evil.invalid/HELP-1") is None


@pytest.mark.asyncio
async def test_environment_mapping_has_precedence(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("JIRA_CONFIG_JSON", json.dumps(MAPPING))
    client = await connector()
    assert isinstance(client, JiraConnector)
    assert client.config["site"] == MAPPING["site"]


def test_config_rejects_unverified_security_and_invalid_site():
    for patch in ({"securityEnforced": True}, {"site": "https://evil.invalid"}, {"projectKey": 'HELP" OR 1=1'}, {"teamOptions": {}}):
        with pytest.raises(ValueError):
            JiraConfig.parse({**MAPPING, **patch})
