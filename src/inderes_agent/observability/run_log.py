"""Per-run trace persistence.

Every query produces a directory at ~/.inderes_agent/runs/<ISO-timestamp>/ containing:
  query.txt          — user question
  routing.json       — router classification (domains, companies, comparison flag, reasoning)
  subagent-N.json    — each subagent's domain, company, model used, full output text, error
  synthesis.txt      — final lead answer
  meta.json          — timing, fallback events, lead model used
  console.log        — stderr capture (HTTP requests, MCP function calls, fallback events)

This gives a complete forensic record without needing to re-run the query.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ..orchestration.workflows import WorkflowResult

RUNS_ROOT = Path.home() / ".inderes_agent" / "runs"


def new_run_dir() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    p = RUNS_ROOT / ts
    p.mkdir(parents=True, exist_ok=True)
    return p


def attach_console_log_handler(run_dir: Path) -> logging.FileHandler:
    """Attach a FileHandler to the root logger so stderr-style logs also hit run_dir/console.log."""
    handler = logging.FileHandler(run_dir / "console.log", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
    )
    logging.getLogger().addHandler(handler)
    return handler


def detach_console_log_handler(handler: logging.FileHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()


def write_run(
    run_dir: Path,
    query: str,
    workflow: WorkflowResult,
    answer: str,
    lead_model: str,
    duration_s: float,
) -> None:
    (run_dir / "query.txt").write_text(query + "\n", encoding="utf-8")

    cls = workflow.classification
    (run_dir / "routing.json").write_text(
        json.dumps(
            {
                "domains": [d.value for d in cls.domains],
                "companies": cls.companies,
                "is_comparison": cls.is_comparison,
                "reasoning": cls.reasoning,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    for i, sr in enumerate(workflow.subagent_results, 1):
        (run_dir / f"subagent-{i:02d}-{sr.domain.value}.json").write_text(
            json.dumps(
                {
                    "index": i,
                    "domain": sr.domain.value,
                    "company": sr.company,
                    "model_used": sr.model_used,
                    "error": sr.error,
                    "text": sr.text,
                    "image_paths": sr.image_paths,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    (run_dir / "synthesis.txt").write_text(answer + "\n", encoding="utf-8")

    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "lead_model": lead_model,
                "duration_seconds": round(duration_s, 3),
                "fallback_events": workflow.fallback_events,
                "subagent_count": len(workflow.subagent_results),
                "subagent_errors": sum(1 for r in workflow.subagent_results if r.error),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
