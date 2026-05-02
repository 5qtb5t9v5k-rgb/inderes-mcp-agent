"""Workflow fan-out semantics: per-company branching, concurrency cap."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from inderes_agent.orchestration import workflows as wf
from inderes_agent.orchestration.router import Domain, QueryClassification


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_comparison_fans_out_per_company(monkeypatch, run_dir):
    """Two companies × one domain = 2 subagent invocations; portfolio domain doesn't fan out."""
    invocations: list[tuple[str, str | None]] = []

    async def fake_run_one(domain, query, company, sem, run_dir):
        invocations.append((domain.value, company))
        return wf.SubagentResult(domain=domain, company=company, text="x", model_used="primary")

    monkeypatch.setattr(wf, "_run_one", fake_run_one)

    cls = QueryClassification(
        domains=[Domain.QUANT, Domain.PORTFOLIO],
        companies=["Konecranes", "Cargotec"],
        is_comparison=True,
        reasoning="",
    )
    result = await wf.run_workflow("compare them", cls, run_dir=run_dir)

    quant_calls = [i for i in invocations if i[0] == "quant"]
    portfolio_calls = [i for i in invocations if i[0] == "portfolio"]
    assert len(quant_calls) == 2  # one per company
    assert len(portfolio_calls) == 1  # no fanout for portfolio
    assert result.classification is cls


@pytest.mark.asyncio
async def test_single_domain_no_fanout(monkeypatch, run_dir):
    invocations: list[tuple[str, str | None]] = []

    async def fake_run_one(domain, query, company, sem, run_dir):
        invocations.append((domain.value, company))
        return wf.SubagentResult(domain=domain, company=company, text="x", model_used="primary")

    monkeypatch.setattr(wf, "_run_one", fake_run_one)

    cls = QueryClassification(
        domains=[Domain.QUANT],
        companies=["Konecranes"],
        is_comparison=False,
        reasoning="",
    )
    await wf.run_workflow("p/e?", cls, run_dir=run_dir)
    assert invocations == [("quant", None)]


@pytest.mark.asyncio
async def test_concurrency_capped(monkeypatch, run_dir):
    """When MAX_CONCURRENT_AGENTS=2, no more than 2 _run_one calls run at once."""
    monkeypatch.setenv("MAX_CONCURRENT_AGENTS", "2")
    # Force settings re-read
    from inderes_agent.settings import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]

    in_flight = 0
    peak = 0

    async def fake_run_one(domain, query, company, sem, run_dir):
        nonlocal in_flight, peak
        async with sem:
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
        return wf.SubagentResult(domain=domain, company=company, text="x", model_used="primary")

    monkeypatch.setattr(wf, "_run_one", fake_run_one)

    cls = QueryClassification(
        domains=[Domain.QUANT, Domain.RESEARCH, Domain.SENTIMENT],
        companies=["A", "B"],
        is_comparison=True,
        reasoning="",
    )
    await wf.run_workflow("q", cls, run_dir=run_dir)

    assert peak <= 2, f"concurrency cap violated: peak={peak}"
