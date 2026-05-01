# Microsoft Agent Framework — primer and how this project uses it

A focused reference on what Microsoft Agent Framework (MAF) is, how this project
uses it, and which capabilities are available for future extension.

For project-specific design decisions see [ARCHITECTURE.md](ARCHITECTURE.md).
For official MAF documentation see [learn.microsoft.com/agent-framework](https://learn.microsoft.com/en-us/agent-framework/).

## Table of contents

- [What MAF is](#what-maf-is)
- [Core abstractions](#core-abstractions)
- [Provider ecosystem](#provider-ecosystem)
- [Orchestration patterns](#orchestration-patterns)
- [Available tool types](#available-tool-types)
- [What this project uses](#what-this-project-uses)
- [What this project intentionally does NOT use](#what-this-project-intentionally-does-not-use)
- [Reference: import map](#reference-import-map)

---

## What MAF is

Microsoft Agent Framework is Microsoft's unified successor to **Semantic Kernel**
and **AutoGen**, released as v1.0 in April 2026 for both .NET and Python. It
provides:

- Single-agent abstractions (chat client + tools + memory)
- Multi-agent orchestration patterns (Sequential, Concurrent, Handoff, GroupChat, Magentic)
- Native MCP (Model Context Protocol) support
- Provider connectors for major LLM vendors (OpenAI, Azure OpenAI, Anthropic, Gemini, AWS Bedrock, Foundry, Ollama, GitHub Copilot)
- Workflow primitives (graph-based execution with checkpointing)
- Built-in OpenTelemetry tracing
- Middleware system for cross-cutting concerns

Design intent: a **production-ready** framework for building agents that talk to
LLMs and call tools. Less academic than research frameworks, more opinionated
than DIY asyncio.

Pip install:

```bash
pip install agent-framework             # core meta-package
pip install agent-framework-gemini --pre # provider connector (alpha)
pip install agent-framework-orchestrations  # workflow builders
```

---

## Core abstractions

| Concept | Purpose | This project uses |
|---|---|---|
| `Agent` | A wrapper around a chat client + tools + system prompt. Has an `async with` lifecycle and a `run(prompt)` method. | ✅ Yes — five `Agent` instances |
| `ChatClient` (protocol) | Provider-specific implementation of the LLM call interface. Has `get_response(messages, *, stream)`. | ✅ Yes — subclassed as `FallbackGeminiChatClient` |
| `FunctionTool` | A callable + JSON schema that an agent can invoke. | ⚪ Indirectly — MCP tools become `FunctionTool` internally |
| `MCPStreamableHTTPTool` | An MCP-server connection that exposes the server's tools as `FunctionTool` instances on the agent. | ✅ Yes — subclassed as `_SanitizingMCPTool` |
| `Workflow` | Graph-based execution with nodes, edges, state, and checkpoints. | ❌ No — we use plain asyncio.gather |
| `Middleware` | Cross-cutting hook around chat-client or function calls. | ❌ No — we subclass instead |
| `ContextProvider` | External memory / RAG injection point. | ❌ No |

### The agent loop

```python
async with Agent(client=..., instructions=..., tools=[...]) as agent:
    result = await agent.run("user question")
    # result.text contains the final answer
```

Internally, the agent does:

1. Send `system_instruction` + user prompt to the chat client
2. If LLM returns a tool call, dispatch to the matching tool, capture result
3. Feed result back to the LLM
4. Repeat until LLM returns text instead of a tool call (or hits a turn limit)

This is **AFC (Automatic Function Calling)** in Gemini terminology — the SDK
hides the tool-call loop from us. We just write the agent and the LLM decides
when to call which tool.

---

## Provider ecosystem

MAF provides "connectors" — packages that adapt provider-specific APIs to
MAF's `ChatClient` protocol. Each is independently pip-installable so you don't
pull in dependencies you don't need.

| Connector package | Provider | Status |
|---|---|---|
| `agent-framework-openai` | OpenAI, Azure OpenAI | Stable |
| `agent-framework-anthropic` | Anthropic Claude | Beta |
| `agent-framework-gemini` | Google Gemini Developer API + Vertex AI | Alpha |
| `agent-framework-bedrock` | AWS Bedrock | Beta |
| `agent-framework-foundry` | Azure AI Foundry | Stable |
| `agent-framework-ollama` | Local Ollama models | Beta |
| `agent-framework-github-copilot` | GitHub Copilot | Beta |
| `agent-framework-claude` | Claude (separate from Anthropic API) | Beta |

Switching providers means changing the chat client used in `Agent(client=...)`.
The agent itself, prompts, tools, and orchestration all stay the same.

---

## Orchestration patterns

MAF provides five orchestration "builders" in `agent_framework.orchestrations`:

| Pattern | When to use | Example |
|---|---|---|
| `SequentialBuilder` | Linear pipeline: agent A → agent B → agent C, each consuming the previous output | Editorial workflow: writer → editor → fact-checker |
| `ConcurrentBuilder` | Same prompt to N agents in parallel; aggregate their outputs | Three perspectives on one question |
| `HandoffBuilder` | Triage agent routes to specialists based on conversation state | Customer support: triage → refunds / orders / returns |
| `GroupChatBuilder` | Multiple agents converse, a "manager" decides who speaks next | Multi-perspective debate with a moderator |
| `MagenticBuilder` | A planner-agent dynamically picks subagents and re-plans on failure | Open-ended research tasks where the workflow isn't known up front |

Each builder hides the asyncio details and gives you a `Workflow` you can
`await` and inspect. Checkpointing is built in for long-running flows.

This project uses **none** of these builders directly; see [the reasoning](#what-this-project-intentionally-does-not-use).

---

## Available tool types

MAF supports several kinds of tools that an agent can call:

### 1. MCP tools (`MCPStreamableHTTPTool`)
External tools exposed via Model Context Protocol over HTTP. Tools and their
JSON schemas are discovered at connection time. **Used by this project for Inderes.**

```python
from agent_framework import MCPStreamableHTTPTool

mcp_tool = MCPStreamableHTTPTool(
    name="my-mcp",
    url="https://example.com/mcp",
    allowed_tools=["tool_a", "tool_b"],   # filter the server's tool list
    approval_mode="never_require",
    http_client=httpx.AsyncClient(auth=...),
    load_prompts=False,
)
```

### 2. Python function tools (`@tool` decorator)
Plain Python callables wrapped as agent tools. The framework introspects
type hints and docstrings to build the JSON schema automatically.

```python
from agent_framework import tool
from typing import Annotated

@tool(approval_mode="never_require")
def calculate_dcf(
    ebit: Annotated[float, "EBIT in millions"],
    growth_rate: Annotated[float, "Long-term growth rate"],
    wacc: Annotated[float, "WACC"],
) -> dict:
    """Calculate fair-value enterprise value with a DCF."""
    ...
```

### 3. Gemini built-in tools (server-side)
Gemini provides server-side tools that don't require any code in our project:

| Method on `GeminiChatClient` | What it provides |
|---|---|
| `get_code_interpreter_tool()` | Sandboxed Python with pandas/numpy/scipy. **Used by this project's quant + portfolio agents.** |
| `get_web_search_tool()` | Google Search grounding |
| `get_maps_grounding_tool()` | Google Maps grounding |
| `get_file_search_tool()` | Vertex AI File Search RAG |
| `get_mcp_tool(name, url, headers, approval_mode)` | Convenience wrapper around `MCPStreamableHTTPTool`, prebound to a specific URL |

These are **server-side** tools: the LLM provider runs them, we just enable them
via the tool list.

### 4. Other agents as tools (`agent.as_tool()`)
You can wrap an agent as a tool another agent can call. This is how MAF
implements the Magentic pattern internally.

### 5. Custom tool subclasses
For advanced cases (auth flows, streaming results, approval gates) you can
subclass MAF's tool base classes. **This project does this** with
`_SanitizingMCPTool` (subclasses `MCPStreamableHTTPTool` to scrub Inderes
schemas before they reach Gemini's validator).

---

## What this project uses

### From `agent_framework` (core)

```python
from agent_framework import Agent, MCPStreamableHTTPTool
```

- `Agent` — five instances (`aino-lead`, `aino-quant`, `aino-research`, `aino-sentiment`, `aino-portfolio`)
- `MCPStreamableHTTPTool` — base class subclassed as `_SanitizingMCPTool` for Inderes integration

### From `agent_framework_gemini`

```python
from agent_framework_gemini import GeminiChatClient
```

- `GeminiChatClient` — subclassed as `FallbackGeminiChatClient` for primary→fallback model selection
- `GeminiChatClient.get_code_interpreter_tool()` — server-side sandboxed Python on quant + portfolio agents

### From `agent_framework`'s observability

- Native OpenTelemetry tracing (MAF emits spans automatically; we just wire up a
  tracer provider in `observability/tracing.py`)
- Standard Python `logging` — captured by our `attach_console_log_handler` for
  per-run forensic logs

---

## What this project intentionally does NOT use

These are MAF capabilities we evaluated but chose not to use, with the reasoning:

| Feature | Why we don't use it |
|---|---|
| **Orchestration builders** (`ConcurrentBuilder`, `HandoffBuilder`, `MagenticBuilder`, etc.) | Per-company fan-out for comparisons must be decided at runtime; free-tier quota requires a hard concurrency cap (semaphore); the orchestration logic is short enough that direct `asyncio.gather` is more readable than wrapping it. |
| **Middleware system** | Middleware operates at the agent level. We need fallback control at the chat-client level — every LLM call regardless of agent. Subclassing `GeminiChatClient` is the right seam. |
| **Workflow** (graph-based) | Overkill for a fixed routing → fan-out → synthesize flow. We don't need stateful nodes, edges, or checkpoints. |
| **Magentic-One dynamic planner** | Our routing is deterministic via a structured-output classifier. A planner is unnecessary for ~7 query types. |
| **Context Providers / Memory** | Conversation memory lives in our `ConversationState` dataclass. It's simpler to keep at the application level. |
| **`Agent.as_tool()`** | Lead reads subagent outputs as text, not as tool calls. Avoids re-running the agent loop one level up. |
| **Other connectors** (Anthropic, OpenAI, Bedrock, Foundry) | Project committed to Gemini. Switching is a one-file change if needed (replace `FallbackGeminiChatClient` with another connector's client). |

---

## Reference: import map

Quick reference for which MAF imports do what.

```python
# Core single-agent
from agent_framework import Agent, MCPStreamableHTTPTool, tool, FunctionTool

# Provider connectors (one of)
from agent_framework_openai import OpenAIChatClient, AzureOpenAIChatClient
from agent_framework_anthropic import AnthropicChatClient
from agent_framework_gemini import GeminiChatClient        # ← THIS PROJECT
from agent_framework_bedrock import BedrockChatClient
from agent_framework_foundry import FoundryChatClient

# Orchestration builders (we don't use these)
from agent_framework.orchestrations import (
    SequentialBuilder,
    ConcurrentBuilder,
    HandoffBuilder,
    GroupChatBuilder,
    MagenticBuilder,
)

# Streaming and types
from agent_framework import AgentResponse, Message, WorkflowEvent

# Function tool helpers
from agent_framework import tool        # decorator for @tool
```

---

## Further reading

- Official Python docs: [learn.microsoft.com/agent-framework/python/](https://learn.microsoft.com/en-us/agent-framework/python/)
- Source repo: [github.com/microsoft/agent-framework](https://github.com/microsoft/agent-framework)
- Orchestration samples: [`python/samples/03-workflows/orchestrations`](https://github.com/microsoft/agent-framework/tree/main/python/samples/03-workflows/orchestrations)
- MCP integration samples: [`python/samples/02-agents/mcp`](https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/mcp)
- Gemini connector samples: [`python/packages/gemini/samples`](https://github.com/microsoft/agent-framework/tree/main/python/packages/gemini/samples)
- Model Context Protocol: [modelcontextprotocol.io](https://modelcontextprotocol.io)
