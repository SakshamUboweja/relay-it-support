"""Review and upload contracts use mock Jira only."""

import copy
import json

import httpx
import pytest

from relay.connector import ConnectorError, DemoConnector, JiraConnector

MAPPING = {
    "site": "https://test.atlassian.net",
    "projectKey": "HELP",
    "serviceDeskId": "1",
    "requestTypeId": "2",
    "generalRequestTypeId": "3",
    "supportTeamFieldId": "customfield_10001",
    "teamOptions": {
        "Service Desk": "10",
        "Identity & Access": "11",
        "Network": "12",
        "Endpoint": "13",
        "Business Applications": "14",
        "Security Review": "15",
    },
    "priorityIds": {"normal": "3", "elevated": "2", "urgent": "1"},
    "reporterMode": "integration-account",
}
DRAFT = {
    "summary": "Monitor flickers",
    "description": "One user affected. Restart did not help.",
    "team": "Endpoint",
    "priority": "normal",
    "restricted": False,
    "externalAccount": None,
    "marker": "relay" + "a" * 32,
}
FIELDS = {
    "requestTypeFields": [
        {
            "fieldId": "summary",
            "name": "What happened?",
            "required": True,
            "jiraSchema": {"type": "string"},
        },
        {
            "fieldId": "description",
            "name": "Details",
            "required": True,
            "jiraSchema": {"type": "string"},
        },
        {
            "fieldId": "asset",
            "name": "Device",
            "required": True,
            "jiraSchema": {"type": "option"},
            "validValues": [
                {"value": "laptop", "label": "Work laptop"},
                {"value": "dock", "label": "USB-C dock"},
            ],
        },
        {
            "fieldId": "attachment",
            "name": "Attachments",
            "required": False,
            "jiraSchema": {"type": "array"},
        },
    ],
    "canRaiseOnBehalfOf": False,
}


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "test@example.invalid")
    monkeypatch.setenv("JIRA_API_TOKEN", "stub-credential")


def api(fields=None, mapping=None, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(
            200,
            json=(fields if fields is not None else FIELDS)
            if request.url.path.endswith("/field")
            else {"name": "Computer support"},
        )

    return JiraConnector(mapping or MAPPING, httpx.MockTransport(handle))


async def test_review_keeps_normal_type_and_exposes_missing_required_field():
    calls = []
    form = await api(requests=calls).review_form(DRAFT)
    assert form["requestTypeId"] == "2"
    assert form["requestTypeName"] == "Computer support"
    assert form["fields"][2] == {
        "id": "asset",
        "label": "Device",
        "required": True,
        "kind": "select",
        "options": [
            {"value": "laptop", "label": "Work laptop"},
            {"value": "dock", "label": "USB-C dock"},
        ],
        "readOnly": False,
        "unsupported": False,
    }
    assert form["attachmentsAllowed"]
    assert "attachment" not in form["values"]
    assert "asset" not in form["values"]
    assert form["values"]["description"].endswith("Intake correlation: " + DRAFT["marker"])
    assert all("/requesttype/3" not in str(request.url) for request in calls)


async def test_required_choice_and_allowlist_are_validated_without_mutation():
    client = api()
    form = await client.review_form(DRAFT)
    with pytest.raises(ConnectorError, match="Device is required"):
        await client.approved_payload(DRAFT, form["values"], "2")
    for bad in ("invented", {"id": "laptop", "extra": True}, ["laptop"]):
        with pytest.raises(ConnectorError, match="invalid option"):
            await client.approved_payload(DRAFT, {**form["values"], "asset": bad}, "2")
    with pytest.raises(ConnectorError, match="outside"):
        await client.approved_payload(
            DRAFT, {**form["values"], "asset": "laptop", "reporter": "other"}, "2"
        )
    with pytest.raises(ConnectorError, match="not configured"):
        await client.approved_payload(DRAFT, form["values"], "999")
    values = {**form["values"], "asset": "laptop"}
    payload = await client.approved_payload(DRAFT, values, "2")
    assert payload["requestFieldValues"] == values
    assert payload["requestTypeId"] == "2"


async def test_approved_create_posts_exact_reviewed_body_once():
    approved_client = api()
    form = await approved_client.review_form(DRAFT)
    values = {**form["values"], "asset": "dock", "summary": "Edited monitor ticket"}
    approved = await approved_client.approved_payload(DRAFT, values, "2")
    posts = []

    def handle(request):
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=FIELDS)
        if "/requesttype/" in request.url.path:
            return httpx.Response(200, json={"name": "Computer support"})
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(201, json={"issueKey": "HELP-1"})
        if "/rest/api/3/issue" in request.url.path:
            return httpx.Response(
                200, json={"fields": {"customfield_10001": {"id": "13"}, "priority": {"id": "3"}}}
            )
        return httpx.Response(200, json={"issueKey": "HELP-1", "currentStatus": {"status": "Open"}})

    client = JiraConnector(MAPPING, httpx.MockTransport(handle))
    await client.create(
        {
            **DRAFT,
            "approvedPayload": approved,
            "approvedSchemaFingerprint": form["schemaFingerprint"],
        }
    )
    assert posts == [approved]
    assert posts[0]["requestFieldValues"]["description"].count("Intake correlation:") == 1


async def test_schema_drift_and_payload_tampering_block_creation():
    client = api()
    form = await client.review_form(DRAFT)
    approved = await client.approved_payload(DRAFT, {**form["values"], "asset": "dock"}, "2")
    changed = copy.deepcopy(FIELDS)
    changed["requestTypeFields"][2]["name"] = "New device wording"
    with pytest.raises(ConnectorError, match="changed after review"):
        await api(changed).payload(
            {
                **DRAFT,
                "approvedPayload": approved,
                "approvedSchemaFingerprint": form["schemaFingerprint"],
            }
        )
    with pytest.raises(ConnectorError, match="no longer matches"):
        await client.payload(
            {**DRAFT, "approvedPayload": {**approved, "raiseOnBehalfOf": "someone"}}
        )
    changed["requestTypeFields"][2]["validValues"] = [{"value": "laptop"}]
    with pytest.raises(ConnectorError, match="invalid option"):
        await api(changed).payload({**DRAFT, "approvedPayload": approved})


async def test_configured_defaults_are_visible_and_readonly_when_not_in_schema():
    mapping = {**MAPPING, "defaults": {"hidden": "configured", "asset": {"id": "dock"}}}
    client = api(mapping=mapping)
    form = await client.review_form(DRAFT)
    assert form["values"]["hidden"] == "configured"
    assert next(f for f in form["fields"] if f["id"] == "hidden")["readOnly"]
    assert (await client.approved_payload(DRAFT, form["values"], "2"))[
        "requestFieldValues"
    ] == form["values"]
    with pytest.raises(ConnectorError, match="read-only"):
        await client.approved_payload(DRAFT, {**form["values"], "hidden": "edited"}, "2")


async def test_unsupported_required_fields_are_reported_and_block_even_with_default():
    fields = copy.deepcopy(FIELDS)
    fields["requestTypeFields"].append(
        {"fieldId": "owner", "name": "Owner", "required": True, "jiraSchema": {"type": "user"}}
    )
    client = api(fields, {**MAPPING, "defaults": {"owner": {"accountId": "abc"}, "asset": "dock"}})
    form = await client.review_form(DRAFT)
    assert form["unsupportedFields"] == ["owner"]
    with pytest.raises(ConnectorError, match="Owner is not supported"):
        await client.approved_payload(DRAFT, form["values"], "2")


async def test_complex_user_choices_stay_unsupported_and_optional_empty_fields_do_not_block():
    fields = copy.deepcopy(FIELDS)
    fields["requestTypeFields"].append(
        {
            "fieldId": "owner",
            "name": "Owner",
            "required": False,
            "jiraSchema": {"type": "user"},
            "validValues": [{"value": "account", "label": "Account"}],
        }
    )
    client = api(fields)
    form = await client.review_form(DRAFT)
    assert form["unsupportedFields"] == ["owner"]
    await client.approved_payload(DRAFT, {**form["values"], "asset": "dock"}, "2")
    with pytest.raises(ConnectorError, match="Owner is not supported"):
        await client.approved_payload(
            DRAFT, {**form["values"], "asset": "dock", "owner": "account"}, "2"
        )


@pytest.mark.parametrize(
    "schema,valid,invalid",
    [
        ({"type": "number"}, 3, True),
        ({"type": "date"}, "2026-09-05", "2026-02-30"),
        ({"type": "string"}, "Actual device", {"madeUp": "nested"}),
    ],
)
async def test_scalar_types_are_validated(schema, valid, invalid):
    fields = copy.deepcopy(FIELDS)
    fields["requestTypeFields"][2] = {
        "fieldId": "asset",
        "name": "Device",
        "required": True,
        "jiraSchema": schema,
    }
    client = api(fields)
    form = await client.review_form(DRAFT)
    await client.approved_payload(DRAFT, {**form["values"], "asset": valid}, "2")
    with pytest.raises(ConnectorError):
        await client.approved_payload(DRAFT, {**form["values"], "asset": invalid}, "2")


async def test_multiselect_and_correlation_validation():
    fields = copy.deepcopy(FIELDS)
    fields["requestTypeFields"][2]["jiraSchema"]["type"] = "array"
    client = api(fields)
    form = await client.review_form(DRAFT)
    assert form["fields"][2]["kind"] == "multiselect"
    values = {**form["values"], "asset": ["dock", "laptop"]}
    await client.approved_payload(DRAFT, values, "2")
    with pytest.raises(ConnectorError, match="correlation"):
        await client.approved_payload(
            DRAFT, {**values, "description": "Edited without marker"}, "2"
        )
    with pytest.raises(ConnectorError):
        await client.approved_payload(DRAFT, {**values, "asset": "dock"}, "2")


async def test_attachment_post_uses_multipart_and_reconciles_exact_filename_and_size():
    requests = []

    def handle(request):
        requests.append(request)
        record = {"id": "123", "filename": "unique-evidence.txt", "size": 4}
        return httpx.Response(
            200, json=[record] if request.method == "POST" else {"fields": {"attachment": [record]}}
        )

    client = JiraConnector(MAPPING, httpx.MockTransport(handle))
    assert (await client.attach("HELP-1", "unique-evidence.txt", b"test", "text/plain"))[
        "id"
    ] == "123"
    request = requests[0]
    assert request.url.path == "/rest/api/3/issue/HELP-1/attachments"
    assert request.headers["X-Atlassian-Token"] == "no-check"
    assert request.headers["Content-Type"].startswith("multipart/form-data;")
    assert b'name="file"; filename="unique-evidence.txt"' in request.content
    assert await client.attachment_exists("HELP-1", "unique-evidence.txt", 4)
    assert not await client.attachment_exists("HELP-1", "other-evidence.txt", 4)
    assert not await client.attachment_exists("HELP-1", "unique-evidence.txt", 5)


async def test_attachment_timeout_is_ambiguous_and_never_retried():
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("lost response", request=request)

    client = JiraConnector(MAPPING, httpx.MockTransport(handle))
    with pytest.raises(ConnectorError) as caught:
        await client.attach("HELP-1", "unique-evidence.txt", b"test", "text/plain")
    assert caught.value.ambiguous
    assert len(requests) == 1


async def test_demo_review_and_attachment_contracts():
    client = DemoConnector()
    form = await client.review_form(DRAFT)
    approved = await client.approved_payload(DRAFT, form["values"], form["requestTypeId"])
    assert approved["requestFieldValues"] == form["values"]
    assert (await client.attach("DEMO-1", "file.txt", b"test", "text/plain"))["size"] == 4
