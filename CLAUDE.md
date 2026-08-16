# CLAUDE.md — inderes-mcp-agent-system

Multi-agent research system over the Inderes Premium dataset. A **learning
project**: the deliverable is the body of transferable patterns, not the tool
itself (`PURPOSE.md`).

This file is deliberately short. The repo's documentation is extensive and
well-organised — this points you into it rather than duplicating it.

## Reading order

Start with the **Documentation map in `README.md`** — it lists every document
with one line on what it contains and who it is for. The path that gives a
complete picture:

1. `README.md` — what it does, how to run it
2. `ARCHITECTURE.md` — the concrete implementation, file by file
3. `MULTI_AGENT_ARCHITECTURE.md` — the generic layered model, with this
   project as a worked example
4. `LESSONS.md` — what was learned, what would be done differently
5. `BACKLOG.md` — what's next, classified by architectural layer

If you need a structured briefing of the whole repo rather than a targeted
change, use **`AI_BRIEFING_PROMPT.md`** — it is a copy-pasteable prompt
written for exactly that, and it is better than improvising a reading plan.

For current state and open work, `BACKLOG.md` and `CHANGELOG.md` are the live
documents. `BUILD_SPEC.md` is historical.

## Anti-capabilities — what the system must never do

These are the project's hard limits, stated in the agent prompts and enforced
by the eval suite. Preserve them in any change; they are not preferences.

- **LEAD never issues a buy or sell recommendation as its own view.**
  Surfacing Inderes' recommendation is the product; generating a new one is
  the line that is not crossed.
- **No agent invents a URL, number, or citation the tool did not return.**
- **QUANT does not predict prices beyond Inderes' published estimates.**
- **LEAD does not call MCP tools itself** — it synthesises over subagent
  output.

A fourth rule follows from the architecture rather than the prompts: **the
valuation figure is computed by deterministic Python** (`valuation/engine.py`),
never by the model. The LLM's job is to produce structured input that
`valuation/parser.py` validates; the engine does the maths. If a change lets
the model produce a fair-value number directly, that change is wrong.

## Attribution and scope

All Inderes analyst content is © Inderes Oyj, and this project is **not
affiliated with Inderes**. Keep that statement intact in the README and in any
user-visible surface. Access depends on a personal Premium subscription.

Also from the README's own non-goals: this is a personal/small-team tool, not
multi-tenant SaaS, and it does not auto-trade. Read-only MCP tools only — if a
write-capable tool is ever added, the governance plane described in
`MULTI_AGENT_ARCHITECTURE.md` (Plane B) must be built first, not afterwards.

## Commands

```bash
uv pip install -e '.[dev]'
pytest -q                          # 375 tests, all mocked, zero live LLM calls
python -m inderes_agent "question" # one-shot CLI
```

CI gates the test suite on every push. Eval structure validation is also
CI-gated; the live LLM-judged evals in `evals/` are run deliberately, not on
every push — structure cheaply, content expensively.

## Shipping gate

- **Always:** `pytest -q` green, with the real output shown. Never claim test
  status without running them.
- **If the change touches routing, synthesis, or prompts:** the eval harness
  in `evals/` exists precisely for this. Prompt changes cannot be judged by
  reading them — "this prompt looks better" is not a measurement.
- **If the change touches valuation:** `tests/valuation/test_excel_parity.py`
  pins the engine to the owner's spreadsheet. If it fails, the engine is
  wrong, not the test.
- **If you changed a test:** justify separately why the test was wrong.
  Changing a test to pass the code is a known failure mode.

## Documentation maintenance

As part of any significant change, without being asked:

- Update `CHANGELOG.md` — what changed and the design rationale, not a commit
  list.
- If something moved between architectural layers, update `ARCHITECTURE.md`;
  if the generic model gained a lesson worth generalising, update
  `MULTI_AGENT_ARCHITECTURE.md`.
- If an error was encountered and fixed, add it to `TROUBLESHOOTING.md` —
  that document's value is that it is exhaustive.
- If a doc and the code disagree, **the code is the truth** — fix the doc in
  the same commit.

Don't create new status documents. The existing set is deliberate.

## Working practice

- Branch + PR always, no direct pushes to main. Branch name `claude/<topic>`.
- Commit messages in English.
- The two sidecar repos (`yahoo-finance-mcp`, `inderes-mcp-auto-relogin`) are
  part of the same functional whole. A change to the MCP contract may affect
  them.
