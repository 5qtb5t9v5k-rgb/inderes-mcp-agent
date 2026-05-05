# Multi-agent system architecture — a layered model

A reference for thinking about non-trivial agentic systems. Generic enough
to apply to any LLM-driven multi-agent service; concrete enough to map
directly to the code in this repository.

> Audience: someone building (or rebuilding) a multi-agent service, who
> has gone past "single agent + a few tools" and is hitting the limits
> of that model.
>
> Companion docs:
> - [`ARCHITECTURE.md`](ARCHITECTURE.md) — how *this* project is currently
>   implemented, file-by-file
> - [`LESSONS.md`](LESSONS.md) — reflections on what was learned along
>   the way
> - [`BACKLOG.md`](BACKLOG.md) — feature ideas, classified by where they
>   sit on the architecture

---

## Table of contents

- [Why a layered model](#why-a-layered-model)
- [The model: five layers + two planes](#the-model-five-layers--two-planes)
- [Layer 1 — Surface](#layer-1--surface)
- [Layer 2 — Brain](#layer-2--brain)
- [Layer 3 — Data](#layer-3--data)
- [Layer 4 — Action](#layer-4--action)
- [Layer 5 — Harness](#layer-5--harness)
- [Plane A — Evals & observability](#plane-a--evals--observability)
- [Plane B — Governance](#plane-b--governance)
- [Memory — a third orthogonal concern](#memory--a-third-orthogonal-concern)
- [How this project maps to the model](#how-this-project-maps-to-the-model)
- [Use case classification by risk](#use-case-classification-by-risk)
- [Now vs. later — evolution path](#now-vs-later--evolution-path)
- [Common pitfalls](#common-pitfalls)
- [Reading order for someone new](#reading-order-for-someone-new)

---

## Why a layered model

Most "build an agent" tutorials produce a single Python file with one
LLM call, a handful of tools, and a `while not done:` loop. That works for
demos. It falls apart fast when:

- More than one agent is involved
- Tool counts grow past ~10
- The system starts to need state (memory, watchlists, history)
- Failures need to be diagnosed after the fact
- Behavior needs to evolve without code changes

At that point you discover — usually painfully — that you've been
mixing concerns. The "agent" code is doing routing, prompt management,
tool definition, state caching, error handling, retry logic, output
formatting, and observability all in one place. None of it is
swappable. None of it is testable in isolation. Any change risks
regressing every other concern.

The fix is the same fix as in any complex software domain: **separate
concerns into layers, define what each layer is responsible for, allow
each layer to evolve independently**.

The model below evolved from building this project plus observing the
common ways multi-agent systems go off the rails. It's not novel —
similar layering ideas appear in Microsoft Agent Framework, the
Anthropic agent SDK, Magentic-One, and others. What's particular here
is the explicit naming of two **planes** (evals, governance) that cut
through all layers, plus calling out the **harness** layer which is
often invisible.

---

## The model: five layers + two planes

```
┌──────────────────────────────────────────────────────────────────┐
│  PLANE B — GOVERNANCE                                            │
│  permission tiers · hard limits · approvals · kill-switch · audit│
└──────────────────────────────────────────────────────────────────┘
            ↕ enforces policy on everything below
┌──────────────────────────────────────────────────────────────────┐
│  ① SURFACE                                                       │
│  Chat · UI · Dashboard · CLI · API endpoints                     │
└──────────────────────────────────────────────────────────────────┘
            ↕
┌──────────────────────────────────────────────────────────────────┐
│  ② BRAIN                                                         │
│  Routing · planning · synthesis · classification                 │
└──────────────────────────────────────────────────────────────────┘
            ↕
┌──────────────────────────────────────────────────────────────────┐
│  ④ ACTION                                                        │
│  Single-agent task · multi-agent task · scheduled workflow       │
└──────────────────────────────────────────────────────────────────┘
            ↕
┌──────────────────────────────────────────────────────────────────┐
│  ③ DATA                                                          │
│  MCP servers · API→MCP adapters · DB (user state, memory)        │
└──────────────────────────────────────────────────────────────────┘
            ↕
┌──────────────────────────────────────────────────────────────────┐
│  ⑤ HARNESS                                                       │
│  Per-agent prompts · tool defs · retry/reflection policies ·     │
│  output formats · anti-capabilities · style                      │
└──────────────────────────────────────────────────────────────────┘
            ↕ defines behavior of every active layer
┌──────────────────────────────────────────────────────────────────┐
│  PLANE A — EVALS & OBSERVABILITY                                 │
│  forensic logs · golden datasets · trajectory eval · feedback    │
└──────────────────────────────────────────────────────────────────┘
            ↕ measures everything above
```

**Layers** are vertical: data flows up from action through brain to
surface, with the harness providing the configuration of how each
layer behaves and the data layer feeding required information.

**Planes** are horizontal: they don't sit between two specific
layers, they cut through all of them. Governance enforces rules on
every layer. Evals observes every layer.

The numbering on the left side reflects the order they appeared in
the original hand-drawn sketch this document is based on. The order
they're explained below is roughly *user-perceived top to actuator
bottom* — surface first because that's what the user touches.

---

## Layer 1 — Surface

### What it is

The surface is **every interaction point between humans (or other
machines) and the system**. Each surface variant has its own
constraints — rendering, latency expectations, payload formats — but
they all converge on the same brain underneath.

Common surface variants:

- **Chat** — conversational, single-turn or multi-turn, free-form input
- **UI** — richer than chat: persona panels, status indicators, click-through actions, embedded visualizations
- **Dashboard** — passive, read-only, aggregates state across many runs
- **CLI** — programmatic, REPL-style, scriptable
- **API** — for other services to consume (rare in early-stage projects, common in mature ones)
- **Notification surface** — push, email, scheduled briefings

### Why it matters

Without an explicit surface layer, your "agent" tends to leak
internals (raw error tracebacks, internal IDs, HTTP request lines)
straight to whoever's looking. With a surface layer, you have one
place to:

- Enforce a public-safe error language (no paths, no commands, no
  internal state)
- Render results consistently (status indicators, badges, source links)
- Hide implementation details (which model handled the call, how many
  retries happened)
- Vary by audience (CLI for developer, UI for end-user, API for service)

### Current state in this project

Three surfaces, all rendering off the same `handle_query()` core:

- `cli/repl.py` — interactive REPL with slash-commands
- `ui/app.py` — Streamlit "Trading Desk" with persona-styled status,
  activity log expander, recommendation badge, follow-up chips,
  clickable source links
- One-shot CLI mode — `python -m inderes_agent "query"`, fire-and-quit

### Future direction

- Dashboard view — list of saved researches, watchlist health, last-run
  status across watched companies
- Mobile-first surface (compressed UI for phone glances)
- API endpoint for programmatic consumption

### Critical pitfalls

- **Leaking error tracebacks**. The first auth-expired card on this
  project showed the full `HeadlessAuthError` text including
  `~/.inderes_agent/tokens.json` — a leak. Fix: every surface masks
  internal exceptions to a public-safe message.
- **Tying surface tightly to brain**. If you write Streamlit-specific
  state into the orchestration code, you can't add a CLI surface
  without rewriting orchestration. Keep surfaces *thin*.
- **Surface defining behavior**. The user-visible "save this query"
  button shouldn't live in the surface. It belongs in the data
  layer. The surface only renders the button.

---

## Layer 2 — Brain

### What it is

The brain decides **what to do** with an incoming intent. It does
not execute — that's the action layer's job. Brain functions
typically include:

- **Routing / classification** — which subagents are relevant
- **Planning** — given the routing, decompose the task into steps
- **Synthesis** — given subagent outputs, combine them into a single
  coherent answer

In a single-agent system, the "brain" and "executor" are often the
same LLM call. In a multi-agent system, separating them is what makes
the architecture comprehensible. Routing decisions become inspectable.
Plans become editable. Synthesis becomes retryable.

### Why it matters

Without a brain layer, agents either:
- Try to do everything themselves (too many tools, accuracy collapses), or
- Get hardcoded to specific workflows (every new query type needs new code)

With a brain layer, the system can introspect on its own decisions.
You can ask: "for this query, the router decided X — was that
correct?" That's a different question from "did the agent produce a
good answer", and answering both questions is what makes a multi-agent
system trustworthy.

### Current state in this project

- `orchestration/router.py` — Gemini structured-output call returns
  `QueryClassification(domains, companies, is_comparison, reasoning)`
- `orchestration/synthesis.py` — LEAD agent reads subagent outputs,
  produces final answer
- LEAD also generates `**💭 Perustelut**` callout — the brain showing
  *why* it synthesized the way it did

The router and synthesis are both "brain" but separated by the
action layer (the actual execution). This is a deliberate choice —
the brain runs at the start (routing/planning) and end (synthesis),
not throughout.

### Future direction

- **Plan-then-execute** (BACKLOG #1) — between routing and execution,
  LEAD writes a structured plan that's user-visible before subagents
  fire. Editable plans become a UX improvement.
- **Reflection** (BACKLOG #2) — between execution and synthesis,
  brain checks for red flags (empty output, anomalous numbers) and
  retries before synthesizing.
- **Disagreement surfacing** (BACKLOG #6) — brain explicitly compares
  subagent outputs against known anchors (e.g., Inderes' published
  estimates) and raises conflicts to the user.

### Critical pitfalls

- **Routing inside an executor**. If your QUANT agent decides "I
  should also call SENTIMENT", you've lost the brain/action split.
  Routing belongs in the brain, before execution.
- **Synthesis without context**. If LEAD synthesizes from prompt-only
  without seeing subagent outputs as text, it's writing fiction.
  Always feed structured subagent results into synthesis.
- **Brain as catch-all**. If the brain ends up doing data fetching,
  state management, and UI rendering, it's no longer a brain. Push
  those down to the data and surface layers.

---

## Layer 3 — Data

### What it is

The data layer is **how the system knows things**. In an LLM agent
context, "knowing" splits into two:

- **External knowledge** — what's in the world: market data, document
  contents, social signals. Reached via tools (MCP, REST APIs).
- **Internal state** — what the *user* or *system* has accumulated:
  watchlists, prior insights, feedback history. Stored in a database.

The data layer's job is to expose both kinds via a clean interface
to the action layer, hiding the source-specific quirks.

### Why it matters

The data layer is where most of the "boring but critical"
infrastructure lives:

- Authentication (OAuth, API keys, service accounts)
- Schema sanitization (different sources, different conventions)
- Caching (don't burn quota on the same call twice)
- Source aggregation (one logical "company fundamentals" might be
  served by three different APIs depending on jurisdiction)

Without an explicit data layer, agents directly query whatever they
were configured with. Adding a new source means modifying every agent.
With a data layer, agents query *capabilities* ("get-fundamentals")
and the data layer routes to the right source.

### Current state in this project

- `mcp/inderes_client.py` — MCP tool factory, schema sanitization,
  bearer-token injection
- `mcp/oauth.py` — OAuth 2.0 + PKCE, token cache, gist mirror,
  rotation-race recovery
- One MCP server (Inderes) — single source for all data

There's no DB yet. State that needs to persist across runs lives in
files (`~/.inderes_agent/runs/`, the gist for tokens, eventually
`feedback.json`). This is fine at single-user scale.

### Future direction

- **Multiple MCP servers** — Inderes, plus hypothetically Twitter,
  Yahoo Finance, internal company data
- **API→MCP adapters** — the user's sketch labels this `API2MCP`.
  Pattern: wrap any non-MCP API in an adapter that exposes it through
  the same interface as the rest of the data layer
- **Database** — for user state (watchlists, saved researches,
  thumbs-up/down history, insight ledger). Doesn't need to be
  Postgres on day one — SQLite or even structured files work
- **Caching layer** — when calls are repeated and expensive, cache
  with TTL

The single-MCP simplicity is currently a feature: you don't need
abstractions you don't have multiple sources for. Build the
abstractions only when adding the second source forces them.

### Critical pitfalls

- **Direct access from brain or surface**. If your Streamlit app
  fetches from MCP directly to render a sidebar, the data layer is
  bypassed. Once that pattern spreads, swapping sources becomes a
  whole-app rewrite.
- **No abstraction over multiple sources**. If you have three sources
  and each agent has if/elif blocks for which to call, you've moved
  the abstraction problem into every agent. Centralize in data layer.
- **Treating state and external knowledge as the same**. They have
  different consistency, retention, and privacy requirements. A
  watchlist (state) needs persistence; a market price (external)
  doesn't. Don't store one in the same place as the other.

---

## Layer 4 — Action

### What it is

The action layer is **execution** — what happens after the brain has
decided what should happen. This is where the actual work occurs:

- **Single-agent task** — one LLM, one focused tool subset, one query.
  Cheap, fast, deterministic-ish. Suitable for "what's Sampo's P/E?"
- **Multi-agent task** — multiple LLMs running in parallel, each
  with its own tool subset. Slower, more expensive, but tackles
  comparative or multi-domain questions ("compare Sampo and Nordea on
  profitability and sentiment")
- **Workflow** — orchestrated multi-step process, often scheduled
  rather than user-triggered. E.g., a nightly briefing that
  iterates over a watchlist, runs a multi-agent fan-out for each,
  and aggregates results

### Why it matters

The brain's decision is "go execute X". Without an explicit action
layer, that decision is implemented inline wherever the brain made
it, which means the brain ends up containing both decision and
execution code. With an action layer, the brain emits a description
of work and the action layer figures out how to do it.

This separation is what enables **scheduled workflows** —
fundamentally the same execution machinery, triggered by cron rather
than user query. And it's what enables **action types to grow**:
adding a new "dual-agent debate" execution mode requires changes
only in the action layer, not the brain.

### Current state in this project

- `orchestration/workflows.py` — `asyncio.gather` + semaphore-bounded
  fan-out
- For comparison queries, action layer fans out per company
- Token-refresh cron in `.github/workflows/refresh-inderes-tokens.yml`
  is a primitive workflow (single-step, scheduled)

### Future direction

- **Scheduled workflows for substantive tasks** — watchlist morning
  briefings, anomaly detection, periodic insight-ledger consolidation
- **Execution-mode variants** — bull-vs-bear debate (BACKLOG #8) is
  a new execution mode at the action layer
- **Workflow composition** — a workflow that calls a multi-agent task
  inside itself

### Critical pitfalls

- **No separation between scheduled and user-triggered work**. If your
  cron-only logic and user-query logic are in different code paths,
  you'll diverge. Same execution machinery should serve both.
- **Hardcoded fan-out**. If "for comparison queries, run 4 agents in
  parallel" is hardcoded, you can't add a new mode without forking
  the executor. Make execution mode a parameter that the brain
  decides.
- **Long-running workflow blocking the surface**. If a watchlist
  briefing runs for 5 minutes, it can't tie up a Streamlit session.
  Workflows should run async with status reporting back to the
  surface.

---

## Layer 5 — Harness

### What it is

This layer is the **invisible** one — most multi-agent system
diagrams omit it entirely. The harness is everything that
**defines how each agent behaves at runtime**, without requiring code
changes to modify.

Components of a complete harness:

- **System prompts** — the agent's role, constraints, output format
- **Few-shot examples** — concrete demonstrations of desired behavior
- **Tool definitions** — which tools this agent has access to,
  including allowed/denied lists per role
- **Output format specs** — exact structure expected from the agent
- **Retry / reflection policies** — when to retry, what red flags to
  retry on, how many attempts
- **Anti-capabilities** — what the agent must *not* do (more on this
  below)
- **Memory rules** — what to remember, what to forget, how to load
  prior context
- **Persona / style** — voice, register, language conventions

### Why it matters

Without an explicit harness layer, all of this lives implicitly in
code. Changing the QUANT agent's output format requires editing a
function. Adding a new anti-capability ("don't recommend buy/sell")
requires adding a string to a prompt that's embedded in code.
Iterating on agent behavior becomes a deploy cycle.

With an explicit harness layer:
- Behavior is data, not code
- Prompts can be versioned independently of the executor
- A/B testing variants becomes possible
- Eval results can be tied to specific harness versions
- Different deployments can run different harnesses without forking
  the codebase

### Anti-capabilities — the underused pattern

Most agent prompts say what the agent *should do*. Strong systems also
say what it *must not do*. Examples from this project:

- LEAD must not recommend buy/sell as its own opinion
- Subagents must not invent URLs the tool didn't return
- QUANT must not predict prices beyond Inderes' estimates
- LEAD must not call MCP tools itself

Anti-capabilities are the mechanism by which you make the system
*safe by default*. Without them, "agent doesn't recommend buy/sell"
is a hope. With them, it's a constraint the prompt enforces and the
eval suite verifies.

### Current state in this project

- `agents/prompts/lead.md`, `quant.md`, `research.md`, `sentiment.md`,
  `portfolio.md` — per-agent system prompts
- `agents/_common.py:today_prompt_prefix()` — date-awareness prefix
- `mcp/inderes_client.py` — tool subsets per agent (`QUANT_TOOLS`,
  `RESEARCH_TOOLS`, etc.) — the "tool definitions" part of harness
- Anti-capabilities live inline in the prompt files (e.g., lead.md's
  "What you do NOT do" section)

This is a **partial** harness. Solid on system prompts and tool
definitions; weak on retry/reflection policies (currently hardcoded)
and memory (no concept yet).

### Future direction

- Extract retry/reflection from code into `harness/reflection.yaml`
  (or similar). When BACKLOG #2 is built, build it as harness, not
  inline code.
- Extract output format expectations into `harness/formats/` so eval
  scripts can verify them mechanically
- Make harness versionable — `harness/v1/quant.md`, `harness/v2/quant.md`,
  with the brain selecting which version to load

### Critical pitfalls

- **Harness logic in executor code**. If your "retry on empty result"
  is hardcoded in workflows.py, you can't change retry behavior
  without code changes. Move it to harness data.
- **Harness without versioning**. If you change a prompt and forget
  what the previous version said, you can't roll back. Harness
  belongs in version control with explicit versions when behavior
  changes meaningfully.
- **Conflating harness with prompts**. Harness is broader — it's all
  the behavior-defining configuration, not just the system prompt
  text.

---

## Plane A — Evals & observability

### What it is

A plane that **cuts through every layer**, observing what happens
and providing the feedback loop for improvement. Components:

- **Forensic logging** — every run captured, every tool call timed,
  every LLM response stored
- **User feedback** — thumbs up/down, optional text comments
- **Smoke tests** — small automated suite of known-good queries
- **Golden datasets** — curated set of queries with known-good
  outputs for regression testing
- **Trajectory evals** — was the right tool called in the right
  order, regardless of final answer text?
- **LLM-as-judge** — another model rates the quality of an answer
  against expected behavior (use cautiously — known unreliable)
- **Production monitoring** — aggregate stats: failure rates,
  thumbs-down ratio, slow queries, expensive queries

### Why it matters

Without an evals plane, every change is fiilis-pohjaista (gut feel).
"This new prompt seems better." Did the success rate go from 78% to
85%? You don't know. With an evals plane, every change is data-
driven. You measure the impact, then decide.

The plane is **horizontal** — not a layer, not a single component.
It needs hooks into:
- Surface (capture user feedback at the moment of evaluation)
- Brain (capture routing decisions for trajectory eval)
- Data (capture which sources were called, at what cost)
- Action (capture per-execution metadata)
- Harness (tie evals to specific harness versions)

### Current state in this project

- ✅ Forensic logging — `~/.inderes_agent/runs/<ts>/` with `query.txt`,
  `routing.json`, `subagent-NN-*.json`, `synthesis.txt`, `meta.json`,
  `console.log`, `narrative.md`
- ❌ User feedback — not yet
- ❌ Smoke tests — not yet (unit tests exist but don't cover end-to-end
  behavior)
- ❌ Golden datasets — not yet
- ❌ LLM-as-judge — not planned
- ❌ Production monitoring — partial (cron worker logs success/fail
  in gist)

### Future direction

The four-level evals pyramid (in order of construction):

1. **Smoke test** (~1 hour to build) — 5 known-good queries asserted
   to route correctly and produce non-empty answers. Pytest, runs in
   CI. Circuit-breaker for regressions.

2. **User feedback** (~2 hours to build) — thumbs up/down on each
   answer in the UI, saved to `feedback.json` per run. Optional
   comment field for thumbs-down. The most valuable signal because
   it's real users on real queries.

3. **Golden dataset** (~1 evening to set up structure, then
   accumulates) — `evals/golden.yaml` lists run IDs of curated
   "this is how it should work" examples. A `replay.py` script
   re-runs them against current code and diffs structure (routing,
   tool calls, key claims) to flag regressions.

4. **Production monitoring** (~ongoing) — aggregate stats over time:
   feedback ratios, mean cost per query, query types that fail most.

### Critical pitfalls

- **Evals as afterthought**. If you build features and *then* try to
  evaluate them, you don't know which part of the change helped.
  Build the eval skeleton early, even if it only has 5 queries.
- **LLM-as-judge as primary signal**. LLM judges are known unreliable.
  They miss subtle errors, agree with confident wrong answers, and
  drift over time. Use only as a *scaling* of human eval, not a
  *replacement*.
- **Diffing raw text in regression**. LLM output varies on word
  choice every run. Diff structure (which tools? which key numbers?
  which claims?), not prose.

---

## Plane B — Governance

### What it is

The plane that **enforces policy** on every layer. Components:

- **Permission tiers per tool** — read-only (low risk),
  modify-state (medium risk), external-action (high risk)
- **Hard limits** — max iterations per query, max cost, max tokens,
  max parallel agents
- **Approval policies** — when an action requires human confirmation
- **Audit trail** — who ran what, when, why (often satisfied by
  forensic logs already)
- **Kill-switch** — a way to halt agent execution mid-flight if
  something is going wrong

### Why it matters

Governance becomes critical when the action layer can affect the
outside world. A read-only research agent has minimal blast radius
— the worst it can do is waste your LLM quota. An agent that can
post tweets, send emails, or place orders has unbounded blast
radius if it goes wrong.

The plane is dormant in read-only systems and active in
write-action systems. Building it before you need it is overkill;
not building it before activating write-actions is reckless.

### Current state in this project

**Effectively dormant.** All MCP tools are read-only (`get-*`,
`list-*`, `search-*`). The worst the agent can do is consume Gemini
quota. The closest to governance we have:

- `MAX_CONCURRENT_AGENTS` semaphore (hard limit on parallelism)
- `DAILY_QUERY_CAP` env var in cloud deploys (hard limit on volume)
- Password-gate on Streamlit app (rudimentary access control)

That's enough for read-only. It's not enough for write-actions.

### Future direction

When (if) the action layer activates write-tools — sending an
email, posting to a forum, placing an order, modifying a watchlist
in someone else's account — the governance plane becomes mandatory:

- Per-tool tier classification with explicit approval thresholds
- "Plan-then-execute with human approval" — brain shows the plan,
  user approves before it runs
- Hard limits on per-action cost (a single trade has a max value)
- Mandatory audit trail tied to user identity
- Kill-switch in the surface layer

### Critical pitfalls

- **Building governance prematurely**. If your tools are all
  read-only, governance complexity adds nothing but overhead. Don't
  build the kill-switch UI before any action can do harm.
- **Activating write-actions without governance**. The opposite
  failure. Don't add a "send tweet" tool and ship it the same day.
- **Governance as toggleable**. If the user can disable governance
  in settings, governance doesn't exist. Governance is the system's
  guarantee, not the user's preference.

---

## Memory — a third orthogonal concern

Not quite a plane, not quite a layer. Memory cuts across the brain
and data layers in a specific way that's worth calling out.

### Four memory tiers

1. **Working memory** — current query's state. Tool results so far,
   intermediate reasoning. Lives in the LLM's context window. Lost
   when the query ends.

2. **Short-term memory** — current conversation. Previous turns in
   the chat. Lives in `ConversationState` or equivalent. Lost when
   the session ends.

3. **Episodic memory** — across sessions. "Last week the user
   researched Sampo's Q3 — it was reportedly weak." Lives in a
   persistent store (gist, DB). Survives sessions. BACKLOG #5
   (insight ledger) is this tier.

4. **Semantic memory** — domain knowledge accumulated over time. "I
   have learned that Finnish small-caps tend to react to Riksbank
   policy with a 1-week lag." Almost no production agent systems
   do this; it requires careful curation and is often not
   distinguishable from the model's pretraining knowledge anyway.

Most agents stop at tier 2. Tier 3 is the next level of usefulness
and the one this project's BACKLOG points toward. Tier 4 is research
territory.

### Critical pitfall

**Conflating tiers**. If you store working-memory tool results in
the same place as episodic insights, you can't distinguish "this
P/E is from the call I made 30 seconds ago" from "this P/E is what
the user noted last week was anomalous". Different tiers, different
retention, different consistency requirements.

---

## How this project maps to the model

Snapshot of where current code sits:

| Model layer | Current implementation |
|---|---|
| Surface | `cli/repl.py`, `cli/render.py`, `ui/app.py`, `ui/components.py`, `ui/theme.css` |
| Brain | `orchestration/router.py`, `orchestration/synthesis.py` |
| Action | `orchestration/workflows.py` |
| Data (external) | `mcp/inderes_client.py`, `mcp/oauth.py` |
| Data (state) | filesystem (`~/.inderes_agent/runs/`, gist for tokens) |
| Harness | `agents/prompts/*.md`, `agents/_common.py`, tool subsets in `mcp/inderes_client.py` |
| Evals plane | `observability/run_log.py`, `observability/narrate.py` (forensic only — no feedback, no golden, no smoke tests) |
| Governance plane | `MAX_CONCURRENT_AGENTS`, `DAILY_QUERY_CAP`, password-gate (minimal) |
| Memory | tier 1 (in LLM context) only; conversation state in REPL is partial tier 2 |

This is a healthy distribution for a single-user read-only research
project. The bare spots — feedback in evals, episodic memory,
governance — are exactly the spots BACKLOG points toward.

---

## Use case classification by risk

When deciding what to build next, classify each candidate by risk
tier. The risk affects which layers and planes need attention.

### 🟢 Low risk — read-only research, signal surfacing

These don't change anything in the world. Worst case: wasted quota,
wrong-but-clearly-cited answer.

- Fanout research across companies ✓ (current MVP)
- Portfolio analysis (your own holdings, your own data)
- Watchlist briefing (BACKLOG #4)
- Anomaly detection (read-only flag-and-report)
- Twitter/news search (more sources, same risk pattern)

What's needed: solid evals plane, decent surface UX. Governance
mostly dormant.

### 🟡 Medium risk — opinion synthesis, model-driven views

These produce *opinions* derived from data. Risk: the opinion is
wrong, gets quoted, and someone makes a decision on it.

- Valuation methods (DCF, comparable multiples) — fine if framed as
  "the model says X under these assumptions", risky if framed as
  "fair value is X"
- Recommendation engine — only safe if it's a *filter over Inderes'
  recommendations*, not a generator of new ones
- Investment style classification — depends on framing; "screen for
  growth-style stocks" is fine, "predict which style will win next"
  is not

What's needed: explicit framing in the surface ("model output, not
financial advice"), strong anti-capabilities in the harness,
disagreement-surfacing in the brain.

### 🔴 High risk — actions that affect the outside world

- Portfolio updates that involve actual trades
- Sending messages on the user's behalf
- Modifying accounts the user doesn't directly own (e.g., a
  shared watchlist used by multiple people)

What's needed: full governance plane, mandatory human approval
flows, hard cost/value caps, audit trail, kill-switch. Don't ship
any of these without the governance plane built first.

For this project specifically, **stay in the green tier**. The
project's stated scope is "research surface, not advisor". That
keeps the governance plane appropriately dormant.

---

## Now vs. later — evolution path

Concrete current state vs. plausible future state of this project:

### Now (v0.2, May 2026)

- Single user, single Inderes Premium subscription
- Read-only MCP tools (16 of them)
- Static fan-out workflow (router → 4 subagents → synthesis)
- Filesystem-based state (per-run logs, gist for tokens)
- No persistent user-specific state
- No automated evaluation; manual inspection only
- Minimal governance (read-only obviates most of it)

### Plausible 6-month-later state

- Same single-user, but multiple data sources (Inderes + at least
  one social/news source)
- API→MCP adapter pattern in the data layer
- Lightweight DB (SQLite or extension of gist pattern) for
  watchlists, saved researches, feedback history, insight ledger
- Brain layer extended with plan-then-execute (BACKLOG #1) and
  reflection-retry (BACKLOG #2)
- Action layer extended with scheduled workflows (watchlist
  briefing, BACKLOG #4)
- Harness layer formalized — `harness/` directory with explicit
  retry policies, format specs, anti-capability lists per agent
- Evals plane operational — golden dataset of 30-50 runs, smoke
  tests in CI, user feedback collected and aggregated
- Memory tier 3 active — insight ledger
- Governance still mostly dormant (still read-only)

### Plausible 12+ month state

- Multi-user (still single-organization, e.g. team of 3-5)
- Persistent DB with user profiles, per-user watchlists
- Action layer extends to write-actions — but with mandatory
  governance plane review
- Possible Plotly-style visualizations as a first-class feature
- Possible bull-vs-bear debate mode (BACKLOG #8) for decision
  queries
- Mobile-friendly surface variant

### Plausible never state

These are categorically excluded:

- Auto-trading without human approval
- Generating "buy" or "sell" recommendations as the agent's own
  view (surfacing Inderes' recommendation is fine; generating new
  ones is the line)
- Predicting prices beyond what Inderes has published
- Multi-tenant SaaS — this is a personal/small-team tool, not a
  product

---

## Common pitfalls

Cross-cutting failure modes worth naming explicitly. Most of these
appeared at some point during this project's development.

### 1. Building the architecture before the use case

If you draw a five-layer architecture and then pick what to build
based on which boxes are empty, you're doing architecture-driven
development. The right path: pick a small, well-scoped use case,
build it end-to-end, *then* notice which layers are doing too much
and refactor.

This project went through this — Yritys 1 and Yritys 2 (in
`LESSONS.md`) failed because the scope was too large for the
architecture to support.

### 2. Underestimating the harness layer

The most consistent surprise across multi-agent projects: how much
of your effort goes into prompts, tool subsets, format definitions,
and edge-case handling. People plan for "build the agents", get
shocked when 60% of their time is harness work, and conclude they
must have done something wrong. They didn't — that's the actual
shape of the work.

### 3. No feedback loop

If you don't have user feedback or golden datasets, every change is
a coin flip. You think it's better; the model produces text that
looks plausible. You don't know if it actually solves more queries
correctly than the previous version. This compounds: after 10
"improvements" you have no idea what the actual quality is.

Fix: build the evals plane skeleton early, even if minimal.

### 4. Governance retrofit

Adding governance after enabling write-actions is much harder than
adding write-actions after building governance. The hard part isn't
the kill-switch — it's the audit trail, the per-tool tiering, the
approval policies. Build them before you have anything to govern,
even if they sit unused.

### 5. Memory entanglement

Storing working memory (this query's tool results) in the same
place as episodic memory (last week's insights) makes both
unmaintainable. Different tiers, different stores.

### 6. Layer leakage

The fastest way to ruin a layered architecture is to let a higher
layer reach down through a lower layer to skip it. "The Streamlit
app is going to call MCP directly because the brain layer doesn't
know how to handle this case yet." That's how layered architectures
turn into spaghetti.

### 7. Demo-driven development

Multi-agent demos are very satisfying to build. They're also
remarkably similar to non-functional production systems. The thing
that makes a system useful is everything that's *not* in the demo:
error recovery, edge cases, observability, regression-proofing.
Plan for at least as much "post-demo" work as "demo" work.

---

## Reading order for someone new

If someone (you, six months from now; a collaborator; a curious
reader) wants to understand this project, this is the path:

1. **`README.md`** — what it does, how to run it
2. **`ARCHITECTURE.md`** — concrete current implementation, file by
   file
3. **`MULTI_AGENT_ARCHITECTURE.md`** *(this document)* — generic
   model and where this project sits within it
4. **`LESSONS.md`** — what was learned in the build, what would be
   done differently
5. **`BACKLOG.md`** — what's next, classified by where it sits in
   the layered model

Reading those five gives a complete picture of: what the system is,
how it's built, why it's built that way, what didn't work, and
where it's heading.

---

*Document version: 2026-05-05 · v1*
