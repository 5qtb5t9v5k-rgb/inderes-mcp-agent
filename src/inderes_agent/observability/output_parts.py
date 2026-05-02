"""Extract structured rendering from an `AgentResponse`.

MAF's `AgentResponse.text` flattens all content parts (including code, code-output
and image data URIs) into a single string. For our display surfaces we want to
keep these structured: code rendered as fenced ```python``` blocks, code output
in a plain code fence, image data saved to disk and referenced by markdown
image syntax.

This module walks `response.messages[*].contents[*]` and produces:
- a single markdown string suitable for display in Streamlit / `narrative.md`
- a list of saved image file paths (relative to `run_dir`)

Function-call / function-result parts (MCP tool invocations) are intentionally
skipped — they are noise from the user's perspective and already captured in
`console.log` for forensic review.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any


_DATA_URI_RE = re.compile(r"^data:(?P<media>[^;,]+)(;base64)?,(?P<payload>.*)$", re.DOTALL)


def _extension_for(media_type: str) -> str:
    """Map common image MIME types to file extensions; default to last segment."""
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/svg+xml": "svg",
        "image/webp": "webp",
    }.get(media_type, media_type.split("/")[-1])


def _save_image(uri: str, target: Path) -> bool:
    """Decode a `data:image/...;base64,...` URI and write to `target`. Returns success."""
    m = _DATA_URI_RE.match(uri or "")
    if not m:
        return False
    payload = m.group("payload")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(payload))
        return True
    except Exception:
        return False


def extract_parts(
    response: Any,
    *,
    run_dir: Path,
    agent_label: str,
) -> tuple[str, list[str]]:
    """Walk the response's content parts and return (markdown, image_paths).

    Args:
        response: An `AgentResponse` (or anything with `.messages` -> `.contents`).
        run_dir: The per-run directory where extracted images will be saved
            under `<run_dir>/images/`. Images are referenced by relative path.
        agent_label: Used to namespace saved image filenames so multiple agents
            in the same run don't collide. Typical: "quant", "research-Sampo".

    Returns:
        - markdown: a string with text, code blocks and image references in order
        - image_paths: list of paths (strings, relative to run_dir) of saved images

    Falls back to `response.text` if the response shape is unexpected.
    """
    messages = getattr(response, "messages", None)
    if messages is None:
        return _fallback_text(response), []

    rendered: list[str] = []
    image_paths: list[str] = []
    image_index = 0
    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "-", agent_label).strip("-") or "agent"

    for msg in messages:
        for content in (getattr(msg, "contents", None) or []):
            ctype = getattr(content, "type", None)

            if ctype == "text":
                text = getattr(content, "text", None)
                if text:
                    rendered.append(text)

            elif ctype == "code_interpreter_tool_call":
                code = _join_text_inputs(getattr(content, "inputs", None) or [])
                if code.strip():
                    rendered.append(f"```python\n{code.rstrip()}\n```")

            elif ctype == "code_interpreter_tool_result":
                outputs = getattr(content, "outputs", None) or []
                stdout_chunks: list[str] = []
                for out in outputs:
                    out_type = getattr(out, "type", None)
                    if out_type == "text":
                        t = getattr(out, "text", None)
                        if t:
                            stdout_chunks.append(t)
                    elif out_type == "data":
                        media = getattr(out, "media_type", "") or ""
                        uri = getattr(out, "uri", "") or ""
                        if media.startswith("image/"):
                            image_index += 1
                            ext = _extension_for(media)
                            rel = f"images/{safe_label}-{image_index}.{ext}"
                            target = run_dir / rel
                            if _save_image(uri, target):
                                image_paths.append(rel)
                                rendered.append(f"![chart]({rel})")
                if stdout_chunks:
                    combined = "".join(stdout_chunks).strip()
                    if combined:
                        rendered.append(f"```\n{combined}\n```")

            # Skip function_call / function_result / mcp_server_* parts —
            # they are MCP tool invocations and already in console.log.
            # text_reasoning, error, usage are also intentionally skipped here;
            # they belong to telemetry, not the user-facing answer.

    md = "\n\n".join(s for s in rendered if s).strip()
    if not md:
        # Fall back to the flattened representation if nothing structured surfaced.
        md = _fallback_text(response)
    return md, image_paths


def _join_text_inputs(inputs: list[Any]) -> str:
    chunks = []
    for inp in inputs:
        if getattr(inp, "type", None) == "text":
            t = getattr(inp, "text", None)
            if t:
                chunks.append(t)
    return "".join(chunks)


def _fallback_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text is not None:
        return text
    return str(response)
