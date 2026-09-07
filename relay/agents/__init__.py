"""Intake pipelines: budgeted model runtime, confidence, cost and trace plumbing."""

import os
from functools import partial

from ..policy import policy
from .orchestrator import run_deterministic, run_multi_agent
from .schemas import Budget
from .single import run_single_agent

PIPELINES = ("deterministic", "single", "multi")


def select_pipeline() -> str:
    name = os.getenv("RELAY_PIPELINE") or "single"
    if name not in PIPELINES:
        raise ValueError("RELAY_PIPELINE must be one of deterministic, single, multi")
    return name


class Pipeline:
    """A named `async (ctx, rt) -> PipelineResult`; the name travels with the injected arm."""

    def __init__(self, name: str, run):
        self.name = name
        self.run = run

    def __call__(self, ctx, rt):
        return self.run(ctx, rt)


def build_pipeline(name: str, *, extract) -> Pipeline:
    """An `async (ctx, rt) -> PipelineResult` for a configured arm; anything else fails at boot."""
    if name == "deterministic":
        return Pipeline(name, partial(run_deterministic, extract=extract))
    if name == "single":
        return Pipeline(name, run_single_agent)
    if name == "multi":
        return Pipeline(name, run_multi_agent)
    raise ValueError("RELAY_PIPELINE must be one of deterministic, single, multi")


def budget_for(name: str) -> Budget:
    try:
        return Budget(**policy["agentBudget"][name])
    except KeyError:
        raise ValueError(f"No agent budget is configured for pipeline '{name}'") from None
