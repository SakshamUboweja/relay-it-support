"""Read-only tools for the triage role: IDs, titles, teams and short snippets, never bodies."""

from dataclasses import dataclass
from typing import Literal

from openai import pydantic_function_tool
from pydantic import BaseModel, ConfigDict, Field

from ..fixtures import catalog
from ..retrieval import lexical
from ..sanitize import sanitize

RESULT_LIMIT = 5
SNIPPET_LIMIT = 240
SEARCH_LIMIT = 25


class CatalogArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CasesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Literal["vpn", "sso", "wifi", "laptop", "atlas"]


class KnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(max_length=200)


@dataclass
class ToolContext:
    sources: list[dict]
    user: dict


def _clean(row: dict) -> dict:
    """Tool results are data for the model; strings still go out redacted."""
    return {key: sanitize(value) if isinstance(value, str) else value for key, value in row.items()}


def build_tools(ctx: ToolContext) -> tuple[list[dict], dict]:
    """Function-tool definitions and their implementations, keyed by tool name."""

    def lookup_catalog(args: CatalogArgs) -> list[dict]:
        return [
            _clean({key: s[key] for key in ("id", "name", "aliases", "team", "critical")})
            for s in catalog
        ]

    def similar_cases(args: CasesArgs) -> list[dict]:
        rows = [
            s
            for s in ctx.sources
            if s.get("kind") == "case"
            and s.get("service") == args.service
            and (s.get("metadata") or {}).get("reviewed") is True
        ]
        return [
            _clean({"id": s["id"], "title": s["title"], "team": s["metadata"].get("team")})
            for s in rows[:RESULT_LIMIT]
        ]

    async def search_knowledge(args: KnowledgeArgs) -> list[dict]:
        rows = await lexical(args.query, ctx.user, limit=SEARCH_LIMIT, incidents=False)
        return [
            _clean(
                {
                    "id": s["id"],
                    "title": s["title"],
                    "service": s["service"],
                    "kind": s["kind"],
                    "snippet": sanitize(s["body"])[:SNIPPET_LIMIT],
                }
            )
            for s in rows[:RESULT_LIMIT]
        ]

    tools = [
        pydantic_function_tool(
            CatalogArgs,
            name="lookup_catalog",
            description="List the supported services with their aliases and owning teams.",
        ),
        pydantic_function_tool(
            CasesArgs,
            name="similar_cases",
            description="Up to five reviewed historical cases for a service, with the team that resolved each.",
        ),
        pydantic_function_tool(
            KnowledgeArgs,
            name="search_knowledge",
            description="Search approved articles and cases the requester may see; returns titles and short snippets.",
        ),
    ]
    return tools, {
        "lookup_catalog": lookup_catalog,
        "similar_cases": similar_cases,
        "search_knowledge": search_knowledge,
    }


def cited_sources(seen: dict[str, dict], ids: list[str]) -> list[dict]:
    """Titles and teams of cited tool results, for the reviewer; unseen IDs are dropped."""
    return [
        {
            "id": id,
            "title": seen[id].get("title") or seen[id].get("name"),
            "team": seen[id].get("team"),
        }
        for id in ids
        if id in seen
    ]
