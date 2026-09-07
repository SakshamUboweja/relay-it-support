"""Intake pipelines: budgeted model runtime, confidence, cost and trace plumbing."""

import os
from functools import partial

from ..policy import policy
from .orchestrator import run_deterministic
from .schemas import Budget
from .single import run_single_agent

PIPELINES = ("deterministic", "single", "multi")


def select_pipeline() -> str:
    name = os.getenv("RELAY_PIPELINE") or "deterministic"
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
    """An `async (ctx, rt) -> PipelineResult`; arms that have not shipped fail at boot."""
    if name == "deterministic":
        return Pipeline(name, partial(run_deterministic, extract=extract))
    if name == "single":
        return Pipeline(name, run_single_agent)
    if name in PIPELINES:
        raise ValueError(f"Pipeline '{name}' is not available in this release")
    raise ValueError("RELAY_PIPELINE must be one of deterministic, single, multi")


def budget_for(name: str) -> Budget:
    try:
        return Budget(**policy["agentBudget"][name])
    except KeyError:
        raise ValueError(f"No agent budget is configured for pipeline '{name}'") from None
