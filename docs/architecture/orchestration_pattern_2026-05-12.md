# Orchestration pattern — Concurrent with conflict resolution + forensic ledger

**Date:** 2026-05-12
**Status:** Draft v1 — mapped to Microsoft Learn's *AI agent orchestration patterns* taxonomy.
**Source taxonomy:** [Microsoft Learn — AI Agent Orchestration Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) (ms.date 2026-02-12)
**Audience:** architects evaluating this system against Microsoft's standard catalogue. Coding agents joining the project who need the "what shape of multi-agent system is this" frame before diving into code.

> This document is the project's positioning against an *external,
> institutional* pattern catalogue. For per-pattern micro-implementation
> mapping (Simon Willison's "Tool Capability Compartmentalization",
> nibzard's full 178-pattern catalogue, etc.) see the complementary
> [`docs/agentic_patterns_mapping_2026-05-11.md`](../agentic_patterns_mapping_2026-05-11.md).
> That doc covers tactical patterns; this one covers the meta-architecture.

---

## Map

- [§1 Where this system sits on the complexity spectrum](#1-where-this-system-sits-on-the-complexity-spectrum)
- [§2 The pattern: concurrent orchestration](#2-the-pattern-concurrent-orchestration)
- [§3 Diagram — Inderes Agent applied to the pattern](#3-diagram--inderes-agent-applied-to-the-pattern)
- [§4 Element-by-element walkthrough](#4-element-by-element-walkthrough)
- [§5 What we add to the textbook pattern](#5-what-we-add-to-the-textbook-pattern)
- [§6 Why not Sequential / Handoff / Magentic](#6-why-not-sequential--handoff--magentic)
- [§7 Future evolution — adding Magentic-style planning](#7-future-evolution--adding-magentic-style-planning)
- [§8 References + cross-doc reading](#8-references--cross-doc-reading)

---

## 1. Where this system sits on the complexity spectrum

Microsoft Learn opens its orchestration-patterns guide with a
deliberately cautious framing: *"Use the lowest level of complexity
that reliably meets your requirements."* Their complexity ladder runs:

1. **Single LLM call** (no agency)
2. **Single agent with tools** (tool-using LLM)
3. **Single agent with retrieval** (RAG)
4. **Multi-agent** (orchestration patterns documented in their guide)

This project was at level 2 in v0.1 (one Gemini, 16 Inderes MCP
tools, a tool-calling loop). It is at level 4 today, with five
specialised subagents + a coordinator. The promotion was justified
because:

- **Token-window scarcity**: a single agent reading 16 tools' outputs
  blew context on cross-domain queries (e.g. "Sampo profit + insiders
  + analyst view"). Five agents reading their own slice fit comfortably.
- **Domain drift**: a single agent that could read forum posts and
  fundamentals tended to bias the synthesis toward whichever data
  arrived first. Per-domain subagents force balanced coverage.
- **Auditability**: per-subagent output is independently inspectable.
  A single agent's chain of thought across 16 tools is not.

Microsoft Learn's caveat applies: the coordination overhead, latency,
and cost of multi-agent are real. We added them deliberately, not by
default. See [§5 *What we add to the textbook pattern*](#5-what-we-add-to-the-textbook-pattern)
for the specific reliability + observability features that justify
the complexity premium.

---

## 2. The pattern: concurrent orchestration

From Microsoft Learn:

> *The concurrent orchestration pattern runs multiple AI agents
> simultaneously on the same task. This approach enables each agent
> to provide independent analysis or processing from its unique
> perspective or specialization. … The results from each agent are
> often aggregated to return a final result … When aggregation is
> needed, choose a strategy that fits the task. For example, vote or
> use majority-rule for classification, apply weighted merging for
> scored recommendations, or use a language-model-synthesized summary
> to reconcile results into a coherent narrative.*

This system uses the **language-model-synthesized summary** variant of
concurrent aggregation, with two refinements:

1. A pre-synthesis **conflict-detector** pass that explicitly
   surfaces disagreements between specialised agents before LEAD
   reads them.
2. A **structural fabrication guard** at the orchestration boundary
   that rejects any subagent output not backed by tool calls,
   preventing hallucinated content from reaching the synthesis stage.

Microsoft Learn's worked example for this pattern is, coincidentally,
a **stock analysis agent** that fans out into Fundamental, Technical,
Sentiment, and ESG specialists — the same shape this project arrived
at independently. Our naming differs (QUANT instead of Fundamental,
RESEARCH instead of Technical-but-our-meaning-is-fundamental-analyst-
content, etc.) but the orchestration structure is essentially
identical.

### When to use this pattern (per Microsoft Learn)

The concurrent pattern is appropriate when:

- The problem benefits from **diverse independent perspectives**
  (different specialisations or knowledge sources)
- Subtasks are **independent and parallelisable**
- **Latency reduction** matters — parallel execution beats serial
- **Comprehensive coverage** of a problem space is required

All four apply here. Stock research is canonically multi-perspective:
fundamentals (QUANT), analyst narrative (RESEARCH), insider +
forum sentiment (SENTIMENT), house portfolio (PORTFOLIO), alternative
valuation (VALUATION). The subagents are independent — none depend
on another's output. Latency is real: serial would be 5× slower.
Coverage matters: missing the SENTIMENT dimension would render the
agent useless on insider-heavy queries.

### When to avoid this pattern

Microsoft Learn calls out scenarios where Concurrent is the wrong
choice:

- **Strong sequential dependencies** (output of agent A is input to
  agent B) → use Sequential / Pipeline pattern instead.
- **Need for real-time dialog among agents** → use Group Chat.
- **Dynamic redirection mid-task based on agent expertise** → use
  Handoff.
- **Open-ended task without a known decomposition** → use Magentic.

None of those apply here. The five domains are stable and parallel.
We re-evaluated this when adding VALUATION — would VALUATION need to
wait for QUANT? No: VALUATION makes its own `get-fundamentals` call,
and the engine math is deterministic, so it parallelises cleanly.

---

## 3. Diagram — Inderes Agent applied to the pattern

This system, drawn in the Microsoft Learn convention (top-down,
specialised agents with their own *Model + knowledge* inputs and
*Intermediate result* outputs):

```
                          ┌──────────────────────┐
                          │   USER QUESTION      │
                          │   (natural language) │
                          └──────────┬───────────┘
                                     ▼
              ┌─────────────────────────────────────────┐
              │              ROUTER (LLM)               │   ← Model
              │   structured-output JSON classification │     + query-classification
              └─────────────────────┬───────────────────┘       prompt
                                    │
                       ┌────────────┴───────────┐
                       ▼                        ▼
                ┌───────────────┐       (skip if planning toggle off)
                │ LEAD-PLANNER  │   ← Model + per-domain
                │ (LLM, opt-in) │     planning instructions
                └───────┬───────┘
                        │
                        ▼  pre-dispatch plan snippets
                        │  injected into each subagent's prompt
                        │
            ────────────┴── FAN-OUT (asyncio.gather, MAX_CONCURRENT_AGENTS=2) ──
            │              │              │              │              │
            ▼              ▼              ▼              ▼              ▼
    ┌───────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │  QUANT    │  │  RESEARCH   │  │  SENTIMENT   │  │  PORTFOLIO   │  │  VALUATION   │
    │   agent   │  │    agent    │  │    agent     │  │    agent     │  │   (opt-in)   │
    │           │  │             │  │              │  │              │  │              │
    │  Model +  │  │  Model +    │  │  Model +     │  │  Model +     │  │  Model +     │
    │  numbers  │  │  narrative  │  │  sentiment   │  │  Inderes own │  │  Greenwald-  │
    │  + esti-  │  │  + tran-    │  │  + insider   │  │  model       │  │  Gordon      │
    │  mates    │  │  scripts    │  │  + forum     │  │  portfolio   │  │  methodology │
    │ knowledge │  │ knowledge   │  │  knowledge   │  │  knowledge   │  │  knowledge   │
    └─────┬─────┘  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
          │               │                │                  │                  │
          │               │                │                  │                  │
          ▼               ▼                ▼                  ▼                  ▼
    Inderes MCP     Inderes MCP      Inderes MCP        Inderes MCP        Inderes MCP
    (3 tools)       (8 tools)        (5 tools)          (3 tools)          (3 tools)
       + opt.         + opt.           + opt.             + opt.            + opt.
    Yahoo (3 tools) Yahoo (2 tools) Yahoo (3 tools)   Yahoo (3 tools)   Yahoo (2 tools)
          │               │                │                  │                  │
          ▼               ▼                ▼                  ▼                  ▼
    Intermediate    Intermediate     Intermediate       Intermediate       Intermediate
    result          result           result             result             result
    (numbers + JSON)(narrative txt) (sentiment summary) (portfolio JSON)   (valuation JSON)
          │               │                │                  │                  │
          └───────┬───────┴────────────────┴──────────────────┴──────────────────┘
                  │
                  ▼
        ┌────────────────────────────┐
        │   FABRICATION GUARD        │  ← Structural rule (not LLM):
        │   (orchestration boundary) │    reject outputs with ≥300 chars
        │                            │    text + ZERO tool calls.
        │                            │    Replace with error=fabricated_no
        │                            │    _tool_calls so downstream synthe-
        │                            │    sis sees an honest failure.
        └─────────────┬──────────────┘
                      ▼
        ┌────────────────────────────┐
        │   CONFLICT DETECTOR (LLM)  │  ← Model + comparison instructions
        │   structured-output JSON   │
        │                            │
        │   { agreements:   [...]    │
        │     conflicts:    [...]    │
        │     isolated_claims: [...] │
        │   }                        │
        └─────────────┬──────────────┘
                      ▼
        ┌────────────────────────────┐
        │   VALUATION ENGINE         │  ← Pure Python, no LLM.
        │   (deterministic, opt-in)  │    Greenwald-Gordon formulas.
        │                            │    Computes fair_value + EPV +
        │                            │    sensitivity_grid(ROE×k) +
        │                            │    sensitivity_grid(g×k).
        └─────────────┬──────────────┘
                      ▼
        ┌────────────────────────────┐
        │   LEAD (synthesis agent)   │  ← Model +
        │                            │    - all subagent outputs
        │                            │    - conflict report
        │                            │    - valuation engine result
        │                            │    - language preference (FI/EN)
        └─────────────┬──────────────┘
                      ▼
        ┌────────────────────────────┐
        │   DECISION WITH SUPPORTING │
        │   EVIDENCE BASED ON        │
        │   COMBINED INTERMEDIATE    │
        │   RESULTS                  │
        │                            │
        │   - Synthesis prose (FI/EN)│
        │   - Recommendation badge   │
        │   - Sensitivity tables     │
        │   - Time-series charts     │
        │   - Footnote sources       │
        │   - Follow-up chips        │
        └────────────────────────────┘

        ALL OF THE ABOVE PERSISTED TO
        ~/.inderes_agent/runs/<ts>/
        as a complete forensic ledger.
```

Compare to Microsoft Learn's stock-analysis example (where a Stock
analysis agent fans out into Fundamental + Technical + Sentiment +
ESG, each labelled with *Model + [specialised] knowledge*). The shape
is identical; this project's additions are the conflict-detector +
fabrication-guard + valuation engine + forensic ledger, which are
documented in §5 as the load-bearing differentiators.

---

## 4. Element-by-element walkthrough

### Router (LLM)

- **Role:** classifies the question into (domains, companies,
  scenario, language). Decides which subset of the 5 subagents to
  invoke.
- **Pattern:** structured-output classification — Gemini returns a
  validated `QueryClassification` Pydantic object.
- **Why an LLM not regex/rules:** Finnish morphology (Sammon /
  Sammosta / Sampo Oyj all map to one company), implicit company
  references in follow-ups, valuation-intent detection (is "mitä
  Sammosta tulisi maksaa" a valuation query — yes; is "vertaa Sampoa
  Nordeaan" — yes only when the user has the toggle on).
- **Model + knowledge:** Gemini-3.1-flash-lite-preview + router prompt
  with explicit domain definitions + 33 keyword stems per domain (in
  the prompt, used as soft anchors not hard matches).

### Lead-planner (LLM, optional)

- **Role:** when the "Suunnitelma ensin" toggle is on, runs an extra
  pass that emits per-domain plan snippets ("for QUANT, focus on Q1
  vs Q1-2024 YoY"). Each snippet is then injected into the relevant
  subagent's system prompt.
- **Pattern:** **Magentic-style task ledger** in miniature. The full
  Magentic pattern (Microsoft Learn) builds a dynamic plan that
  evolves; this is a one-shot pre-dispatch plan. The simpler scope
  fits the bounded query space.
- **Why optional:** adds ~3 seconds latency. For one-off questions
  it's overhead; for multi-domain comparisons it improves coverage
  meaningfully.

### Five specialised agents (concurrent)

Each subagent has:

- **Its own LLM instance** (Gemini-3.1-flash-lite-preview by default,
  upgradeable to Pro per the "Tarkka kaikki" tier toggle)
- **A bounded tool subset** from Inderes MCP — enforced at
  construction time via `MCPStreamableHTTPTool(allowed_tools=...)`
- **An optional Yahoo MCP tool subset** when `YAHOO_MCP_URL` is set
  *(currently local-only)*
- **A persona prompt** in `src/inderes_agent/agents/prompts/*.md`
  (e.g. `quant.md`, `sentiment.md`) that establishes domain
  responsibilities + the structural hard gate against tool-less
  output
- **A "model and [knowledge]" labelling convention** matching MS
  Learn's diagram convention:

| Agent | Model | Knowledge (= prompt + tool subset) |
|---|---|---|
| QUANT | Flash-lite-preview *(default)* | Inderes fundamentals + estimates + search |
| RESEARCH | Flash-lite-preview | Inderes content + transcripts + documents + search |
| SENTIMENT | Flash-lite-preview | Inderes insider transactions + forum + calendar |
| PORTFOLIO | Flash-lite-preview | Inderes model-portfolio content + price |
| VALUATION *(opt-in)* | Flash-lite-preview | Inderes fundamentals + estimates + Greenwald-Gordon prompt |
| LEAD | Flash *(upgraded)* | Subagent outputs + conflict report + Trading-Desk synthesis instructions |
| Conflict detector | Flash-lite-preview | Subagent comparison schema |

Each subagent produces an **intermediate result** as Microsoft Learn
labels it: a structured payload containing
`text` (the prose answer) + `tool_calls[]` (with names, args,
result summaries) + `reasoning` (the "Ajatus:" block, parsed
separately).

### Fabrication guard

- **Role:** structural rejection of subagent outputs that emitted
  ≥300 characters of domain-loaded text but ZERO MCP tool calls.
  Pure Python check, no LLM.
- **Why this exists:** observed empirically. The hard gate in the
  prompt catches ~95% of would-be fabrications, but Gemini Flash-
  Lite occasionally slips through and emits a complete-looking
  fabricated answer. The orchestration-tier guard catches the
  remaining ~5% deterministically.
- **What happens when triggered:** the subagent's result is
  replaced with `error="fabricated_no_tool_calls"`. LEAD sees an
  honest failure rather than a plausible-looking invention. When
  ALL subagents fail this way, the workflow short-circuits to a
  fixed "I didn't find the company" message instead of synthesising
  on top of fabrication.

### Conflict detector (LLM)

- **Role:** pre-synthesis pass that reads all subagent outputs and
  emits structured JSON describing where they agree, disagree, or
  where a claim appears in only one subagent ("isolated claim").
- **Pattern:** this is **not** in Microsoft Learn's catalogue
  explicitly, but maps to the "language-model-synthesized
  reconciliation" variant of concurrent aggregation that they
  mention generically. Making it a separate LLM call gives:
  - Independent reasoning surface (the conflict reasoning isn't
    contaminated by the LEAD prompt's "write a nice answer" framing)
  - JSON output (machine-readable, auditable, persisted to
    `conflicts.json`)
  - Easier to swap models or improve later
- **Output shape:**
  ```json
  {
    "agreements": [
      {"claim": "Sampo's recommendation is LISÄÄ", "subagents": [1, 3]}
    ],
    "conflicts": [
      {"claim_a": {...}, "claim_b": {...}, "subagent_a": 1, "subagent_b": 2,
       "resolution_hint": "Inderes is more recent; trust QUANT"}
    ],
    "isolated_claims": [...]
  }
  ```

### Valuation engine (deterministic, not LLM)

- **Role:** when VALUATION subagent emitted parseable JSON, run the
  pure-Python Greenwald-Gordon engine against those inputs. Produces
  a `Valuation` dataclass with fair_value + EPV + quality
  classification, plus two sensitivity grids (5×5 each for ROE × k
  and g × k).
- **Why this is a separate layer:** mathematics is deterministic.
  LLMs are not. Putting the math in pure Python with 49 unit tests
  + 20 Excel-parity tests means the FAIR VALUE NUMBER itself never
  drifts with model versions. The agent's job is to source the
  inputs from MCP; the engine's job is to do the math; LEAD's job
  is to narrate around them.

### LEAD (synthesis)

- **Role:** reads all the upstream artefacts and writes the final
  answer the user sees. NOT a router. NOT a planner. A synthesiser.
- **Inputs (Model + knowledge labels in MS Learn style):**
  - Model: Gemini 2.5 Flash (one tier up from subagents — synthesis
    is the highest-quality LLM step)
  - Subagent outputs (text + tool-call summaries)
  - Conflict report (JSON, agreement / conflict / isolated)
  - Valuation engine result + sensitivity grids (when applicable)
  - User language preference (FI / EN)
- **Output:** prose answer (markdown), structured `paattely.json`
  reasoning block, optional follow-up suggestion chips, sensitivity
  tables expander (rendered by UI from `valuation.json`).

### Forensic ledger

Every run writes a complete record to
`~/.inderes_agent/runs/<timestamp>/`. This is the part Microsoft
Learn doesn't address — their diagrams stop at "Result". Ours
continues: each intermediate step persists to disk in structured
form. See `ARCHITECTURE.md` "Observability" section for the file
layout. Discussed further in [§5](#5-what-we-add-to-the-textbook-pattern).

---

## 5. What we add to the textbook pattern

Microsoft Learn's diagram for concurrent orchestration is correct but
minimal. Three additions in this project make the pattern reliably
usable:

### 5.1 Trust + reliability layer (three components)

The textbook pattern says nothing about what to do when a specialised
agent fabricates. In a production-grade system this is the single
most common failure mode.

- **Prompt-side hard gate.** Every subagent's prompt begins with an
  explicit block: *"Numbers from training memory are FORBIDDEN. A
  response with ZERO MCP tool calls is automatically rejected as
  fabrication."* This catches ~95% of would-be fabrications at the
  source.
- **Structural fabrication guard at the orchestration boundary.**
  When the prompt fails, the workflow code (`workflows.py:_detect
  _fabrication`) rejects ≥300-char output with zero tool calls and
  replaces it with an error. Pure Python, deterministic. Catches the
  remaining ~5%.
- **Conflict detector as a separate LLM call.** Surfaces
  disagreements explicitly rather than letting them be implicit in
  LEAD's training-data priors. Makes the pattern's emergent
  self-correction property documentable + replayable.

### 5.2 Deterministic compute alongside LLM reasoning

The valuation engine is pure Python. Microsoft Learn's concurrent
pattern describes LLM aggregation; we add a *deterministic math
layer* that runs on the LLM's JSON output. This separates two
concerns:

- **LLM job:** fetch inputs from MCP (BVPS, ROE, growth, price)
- **Engine job:** compute fair value, EPV, sensitivity grids — math
  with 100% reproducibility

This is a generalisable insight: when your domain has deterministic
math (valuation, options pricing, statistical tests), don't ask the
LLM to do it. Ask the LLM to source the inputs, then run the math in
pure code.

### 5.3 Forensic ledger persisted per run

Microsoft Learn's diagrams stop at "Result". Our diagrams continue
to the ledger. Every run writes:

```
runs/<timestamp>/
  query.txt              ← user's question
  routing.json           ← router's decision + reasoning
  plan.json              ← (opt-in) lead-planner output
  subagent-NN-<role>.json  ← per-subagent: text + tool_calls + reasoning
  conflicts.json         ← conflict-detector's structured output
  valuation.json         ← (opt-in) agent inputs + engine results
                           + sensitivity grids
  paattely.json          ← LEAD's structured reasoning block
  synthesis.txt          ← final answer the user saw
  meta.json              ← duration + costs + error counts
  console.log            ← raw stderr / log lines (HTTP, fallback, etc.)
  narrative.md           ← auto-generated human-readable timeline
  feedback.json          ← (opt-in) user's 👍/👎 + free text
```

`narrative.md` is the human-readable replay: open it to see what
happened, when, with which model, in which order, producing which
intermediate result. This is the difference between *"trust the
answer"* and *"audit the answer"*. Microsoft Learn's catalogue
doesn't require this; we made it load-bearing.

---

## 6. Why not Sequential / Handoff / Magentic

We evaluated each of the other four MS Learn patterns and chose
against them, deliberately:

### Sequential / Pipeline

- **Why considered:** if QUANT's output feeds RESEARCH which feeds
  SENTIMENT, the chain could improve quality through progressive
  refinement.
- **Why rejected:** subagents have no real dependencies on each other
  for typical queries. Forcing a pipeline would 5× the latency for
  marginal quality gain. Microsoft Learn cautions: *"Use Sequential
  for problems with clear dependencies."* We don't have them.
- **Where we do use sequential-style chaining:** conflict-detector →
  LEAD synthesis is a 2-step pipeline. This is appropriate because
  the conflict detector's output is genuinely a precondition for
  good synthesis.

### Group chat

- **Why considered:** the agents could discuss their findings,
  iterate, refine collaboratively.
- **Why rejected:** would multiply LLM costs by an order of magnitude
  for a single query. The agents don't really *talk* to each other
  anyway — they specialise on disjoint domains. The conflict detector
  achieves what group chat would achieve (surface disagreements)
  without the multi-turn cost. Microsoft Learn: *"Avoid Group Chat
  when latency or cost is a hard constraint."*

### Handoff

- **Why considered:** the router could be a first agent that decides
  which specialised agent should handle the query, then hands off to
  that one.
- **Why rejected:** Inderes-research queries almost always benefit
  from multiple perspectives. Handoff to a single specialist would
  produce the same kind of one-perspective answer that single-agent
  v0.1 produced badly. The router's job here is to choose the
  *parallel subset*, not to pick a single delegate.

### Magentic

- **Why considered:** open-ended problems with no predetermined
  decomposition.
- **Why rejected:** the problem space here IS predetermined.
  Stock-research queries decompose into the same five domains every
  time. Magentic's dynamic task ledger would be over-engineering for
  a stable decomposition.
- **Where we do borrow from Magentic:** the *Lead-planner toggle*
  (when on, emits per-domain plan snippets before dispatching). It's
  Magentic-in-miniature: one-shot plan emission rather than
  iterative ledger refinement.

---

## 7. Future evolution — adding Magentic-style planning

The current concurrent + lead-planner combination handles the bounded
query space (single-company / multi-company comparison / sector
queries / valuation requests). When the system expands to support:

- **Open-ended research questions** ("how would Nordic banks respond
  to a 200bp rate cut")
- **Multi-step deep dives** ("build me an analyst report on Sampo
  covering thesis + risks + scenarios + 5-year forward view")
- **Sustained workflows** (tomorrow's planned leaderboard mode where
  the agent monitors a watchlist and decides which name to dig into)

— a fuller Magentic-style task ledger becomes appropriate. The
relevant Microsoft Learn pattern then is *"magentic with concurrent
sub-tasks"*: the manager builds a plan that, at each step, may invoke
the existing concurrent pattern on a sub-question.

This is BACKLOG `§ 1` *"Auto-orchestrator (Magentic ledger)"* and
not in the next-30-day roadmap. The trigger to invest in it is the
moment the user starts asking questions that don't fit the current
five-domain decomposition cleanly.

---

## 8. References + cross-doc reading

### Authoritative sources

- [Microsoft Learn — AI Agent Orchestration Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) — the taxonomy this document maps to. ms.date 2026-02-12.
- [Cloud Design Patterns — Pipes and Filters](https://learn.microsoft.com/en-us/azure/architecture/patterns/pipes-and-filters) — the cloud pattern that Sequential resembles.
- [Cloud Design Patterns — Fan-out/Fan-in](https://learn.microsoft.com/en-us/azure/architecture/patterns/) — the cloud pattern that Concurrent resembles.

### Project documents — recommended reading order

For deeper detail on this project's instantiation:

1. [`README.md`](../../README.md) — quick orientation + dual-MCP architecture overview
2. [`ARCHITECTURE.md`](../../ARCHITECTURE.md) — file-by-file mapping
3. [`MULTI_AGENT_ARCHITECTURE.md`](../../MULTI_AGENT_ARCHITECTURE.md) — generic layered model (Surface / Brain / Action / Data / Harness)
4. [`docs/strategy/themes_and_stories_2026-05-12.md`](../strategy/themes_and_stories_2026-05-12.md) — five-theme product strategy frame
5. [`docs/agentic_patterns_mapping_2026-05-11.md`](../agentic_patterns_mapping_2026-05-11.md) — micro-pattern mapping against nibzard's 178-pattern catalogue
6. [`docs/design_briefs/company_leaderboard_2026-05-12.md`](../design_briefs/company_leaderboard_2026-05-12.md) — the next major product direction
7. `LESSONS.md` → *"MAF is a useful primitive, not a finished framework"* — concrete subclasses + helpers we had to write because the framework didn't provide them

### Microsoft Learn patterns NOT used here

Documented for the "we considered this, chose against it" trail
(see [§6](#6-why-not-sequential--handoff--magentic) for reasoning):

- Sequential / Pipeline
- Group chat
- Handoff
- Magentic *(considered for future)*

---

*This document is a draft v1 — produced as a 1-shot mapping against
the Microsoft Learn pattern catalogue. Update in place as the
orchestration evolves; consider re-validating against MS Learn's
catalogue annually (their pattern set evolves too — the page above
is updated periodically with new patterns or refinements).*
