"""Lead synthesis: feed subagent outputs to aino-lead, get a final answer."""

from __future__ import annotations

from ..agents import build_lead_agent
from .workflows import WorkflowResult


def _format_subagent_results(result: WorkflowResult) -> str:
    """Render subagent outputs as a structured prompt for the lead."""
    lines: list[str] = []
    for i, sr in enumerate(result.subagent_results, 1):
        header = f"### {i}. {sr.domain.value}"
        if sr.company:
            header += f" — {sr.company}"
        header += f"  [model: {sr.model_used}]"
        lines.append(header)
        if sr.error:
            lines.append(f"_subagent error: {sr.error}_")
        else:
            lines.append(sr.text or "_(empty response)_")
        lines.append("")
    return "\n".join(lines)


async def synthesize(query: str, workflow_result: WorkflowResult) -> tuple[str, str]:
    """Run the lead agent over the subagents' outputs.

    Returns (final_answer_text, lead_model_used).
    """
    from ..agents._common import today_prompt_prefix

    subagents_block = _format_subagent_results(workflow_result)
    cls = workflow_result.classification

    prompt = today_prompt_prefix() + f"""\
USER QUESTION:
{query}

ROUTING DECISION:
domains   = {[d.value for d in cls.domains]}
companies = {cls.companies}
comparison = {cls.is_comparison}
reasoning = {cls.reasoning}

SUBAGENT OUTPUTS:
{subagents_block}

Now synthesize a final answer for the user, following your instructions.
"""

    async with build_lead_agent() as lead:
        result = await lead.run(prompt)
        text = result.text if hasattr(result, "text") else str(result)
        model_used = getattr(lead.client, "last_used_model", "unknown")
        return text, model_used
