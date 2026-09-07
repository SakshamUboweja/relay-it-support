"""Read-only tools hand the model IDs, titles, teams and snippets, never a source body."""

from datetime import datetime, timezone

import pytest

from relay.agents.tools import (
    CasesArgs,
    CatalogArgs,
    KnowledgeArgs,
    ToolContext,
    build_tools,
    cited_sources,
)
from relay.cli import seed_demo
from relay.db import query
from relay.fixtures import catalog, users
from relay.retrieval import lexical, retrieve

MAYA, OPERATOR = users[0], users[2]


def case(n, service="vpn", team="Network", reviewed=True, kind="case", title=None):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": f"case-{n}",
        "kind": kind,
        "title": title or f"{service} case {n}",
        "body": "Full historical body that must never reach the model.",
        "service": service,
        "visibility": "all",
        "status": "approved",
        "metadata": {"reviewed": reviewed, "team": team},
        "created_at": now,
        "updated_at": now,
    }


def tools_for(sources=None, user=MAYA):
    return build_tools(ToolContext(sources=sources or [], user=user))


def test_tools_are_strict_function_tools_with_matching_implementations():
    tools, impls = tools_for()
    names = [t["function"]["name"] for t in tools]
    assert names == ["lookup_catalog", "similar_cases", "search_knowledge"] == list(impls)
    for tool in tools:
        assert tool["type"] == "function" and tool["function"]["strict"] is True
        assert tool["function"]["parameters"]["additionalProperties"] is False
        assert tool["function"]["description"]
    assert tools[2]["function"]["parameters"]["properties"]["query"]["maxLength"] == 200


def test_lookup_catalog_returns_only_catalog_fields():
    tools, impls = tools_for()
    rows = impls["lookup_catalog"](CatalogArgs())
    assert rows == [
        {
            "id": s["id"],
            "name": s["name"],
            "aliases": s["aliases"],
            "team": s["team"],
            "critical": s["critical"],
        }
        for s in catalog
    ]


def test_similar_cases_filters_by_service_and_reviewed_and_caps_at_five():
    sources = [case(n) for n in range(7)]
    sources += [case(7, service="wifi"), case(8, reviewed=False), case(9, kind="article")]
    tools, impls = tools_for(sources)
    rows = impls["similar_cases"](CasesArgs(service="vpn"))
    assert [r["id"] for r in rows] == [f"case-{n}" for n in range(5)]
    assert rows[0] == {"id": "case-0", "title": "vpn case 0", "team": "Network"}
    assert all(set(r) == {"id", "title", "team"} for r in rows)
    assert impls["similar_cases"](CasesArgs(service="wifi")) == [
        {"id": "case-7", "title": "wifi case 7", "team": "Network"}
    ]
    assert impls["similar_cases"](CasesArgs(service="atlas")) == []


def test_similar_cases_sanitizes_titles():
    tools, impls = tools_for([case(1, title="VPN password: hunter2 rejected")])
    rows = impls["similar_cases"](CasesArgs(service="vpn"))
    assert rows[0]["title"] == "VPN password: [REDACTED] rejected"


def test_cited_sources_resolve_titles_and_teams_of_seen_results_only():
    seen = {
        "case-1": {"id": "case-1", "title": "VPN case", "team": "Network"},
        "kb-1": {"id": "kb-1", "title": "VPN article", "service": "vpn", "kind": "article"},
        "vpn": {"id": "vpn", "name": "Corporate VPN", "team": "Network", "critical": False},
    }
    assert cited_sources(seen, ["kb-1", "case-1", "vpn", "case-9"]) == [
        {"id": "kb-1", "title": "VPN article", "team": None},
        {"id": "case-1", "title": "VPN case", "team": "Network"},
        {"id": "vpn", "title": "Corporate VPN", "team": "Network"},
    ]


@pytest.mark.usefixtures("isolated_db")
async def test_search_knowledge_returns_visibility_scoped_snippets_never_bodies():
    await seed_demo()
    body = "VPN connection failure guidance. " * 20
    for id, visibility in (("kb-london", "london"), ("kb-all", "all")):
        await query(
            "INSERT INTO sources(id,kind,title,body,service,visibility,metadata) VALUES($1,'article',$2,$3,'vpn',$4,'{}')",
            [id, f"VPN connection failure ({visibility})", body + " token sk-abcdefghijklmnop", visibility],
        )
    tools, impls = tools_for(user=MAYA)
    rows = await impls["search_knowledge"](KnowledgeArgs(query="VPN connection failure"))
    assert 0 < len(rows) <= 5
    assert all(set(r) == {"id", "title", "service", "kind", "snippet"} for r in rows)
    assert all(len(r["snippet"]) <= 240 for r in rows)
    ids = [r["id"] for r in rows]
    assert "kb-all" in ids and "kb-london" not in ids
    hit = next(r for r in rows if r["id"] == "kb-all")
    assert hit["snippet"] == body[:240] and "sk-abcdef" not in hit["snippet"]
    tools, impls = tools_for(user=OPERATOR)
    rows = await impls["search_knowledge"](KnowledgeArgs(query="VPN connection failure london"))
    assert "kb-london" in [r["id"] for r in rows]


@pytest.mark.usefixtures("isolated_db")
async def test_lexical_shares_its_sql_with_retrieve():
    await seed_demo()
    text = "VPN connection failure"
    assert [s["id"] for s in await retrieve(text, MAYA)] == [
        s["id"] for s in await lexical(text, MAYA)
    ]
    assert any(s["kind"] == "incident" for s in await lexical(text, MAYA))
    scoped = await lexical(text, MAYA, limit=25, incidents=False)
    assert len(scoped) == 25
    assert all(s["kind"] in ("article", "case") for s in scoped)
    assert [s["id"] for s in scoped] == [
        s["id"] for s in await lexical(text, MAYA) if s["kind"] != "incident"
    ][:25]
