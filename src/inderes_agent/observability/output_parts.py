"""Extract structured rendering from an `AgentResponse`.

`agent_framework_gemini` collapses Gemini's distinct response parts into plain
`Content.from_text(...)` objects:
  - `executable_code` (the Python the model wanted to run) → text
  - `code_execution_result.output` (the stdout)            → text
  - `inline_data` (matplotlib figures etc.)                → DROPPED entirely

That collapse is the reason `result.text` produces an unstructured blob with
code, output and prose merged together. To recover structure we inspect each
Content's `raw_representation` (the original Gemini `Part`) and re-wrap:
  - executable_code  → ```python ... ```
  - execution result → ``` ... ```
  - actual text      → rendered as-is

Images cannot currently be recovered: `agent_framework_gemini` doesn't surface
`inline_data` parts at all. Sandboxed `plt.show()` figures are lost upstream.
We compensate by stripping any `![alt](filename)` markdown that the agent
wrote referencing files it `savefig()`'d into the sandbox FS — those would
render as broken icons in Streamlit. If we ever capture inline_data properly,
this stripping can be relaxed.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any


_DATA_URI_RE = re.compile(r"^data:(?P<media>[^;,]+)(;base64)?,(?P<payload>.*)$", re.DOTALL)
_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)\s*")


def _extension_for(media_type: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/svg+xml": "svg",
        "image/webp": "webp",
    }.get(media_type, media_type.split("/")[-1])


def _save_image(uri: str, target: Path) -> bool:
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


def _classify_text_content(content: Any) -> str:
    """Inspect raw_representation to classify a 'text' Content.

    Returns one of: 'code', 'code_output', 'text'.
    """
    raw = getattr(content, "raw_representation", None)
    if raw is None:
        return "text"
    # Gemini Part objects have these optional fields.
    if getattr(raw, "executable_code", None) is not None:
        return "code"
    if getattr(raw, "code_execution_result", None) is not None:
        return "code_output"
    return "text"


def _strip_dangling_image_refs(text: str, kept_images: list[str]) -> str:
    """Remove `![alt](path)` references that don't point to a known image file.

    Agents using `plt.savefig('foo.png')` then writing `![chart](foo.png)` create
    references to files that exist only inside the sandbox. Those would render
    as broken icons. We keep references whose path appears in `kept_images`
    (the images we actually extracted and saved).
    """
    kept_set = {str(p) for p in kept_images}

    def _keep_or_strip(m: re.Match[str]) -> str:
        path = m.group(1).strip()
        return m.group(0) if path in kept_set else ""

    return _IMG_REF_RE.sub(_keep_or_strip, text)


def extract_parts(
    response: Any,
    *,
    run_dir: Path,
    agent_label: str,
) -> tuple[str, list[str]]:
    """Walk the response's content parts and return (markdown, image_paths)."""
    messages = getattr(response, "messages", None)
    if messages is None:
        return _strip_dangling_image_refs(_fallback_text(response), []), []

    rendered: list[str] = []
    image_paths: list[str] = []
    image_index = 0
    safe_label = re.sub(r"[^A-Za-z0-9_.-]", "-", agent_label).strip("-") or "agent"

    for msg in messages:
        for content in (getattr(msg, "contents", None) or []):
            ctype = getattr(content, "type", None)

            if ctype == "text":
                text = getattr(content, "text", None) or ""
                if not text.strip():
                    continue
                kind = _classify_text_content(content)
                if kind == "code":
                    rendered.append(f"```python\n{text.rstrip()}\n```")
                elif kind == "code_output":
                    rendered.append(f"```\n{text.rstrip()}\n```")
                else:
                    rendered.append(text)

            elif ctype == "data":
                # Future: when agent_framework_gemini supports inline_data,
                # images will arrive here. For now this branch may never fire
                # with the current connector, but kept for forward compat.
                media = getattr(content, "media_type", "") or ""
                uri = getattr(content, "uri", "") or ""
                if media.startswith("image/"):
                    image_index += 1
                    ext = _extension_for(media)
                    rel = f"images/{safe_label}-{image_index}.{ext}"
                    target = run_dir / rel
                    if _save_image(uri, target):
                        image_paths.append(rel)
                        rendered.append(f"![chart]({rel})")

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

            # function_call / function_result / mcp_server_* / text_reasoning /
            # error / usage parts intentionally skipped.

    md = "\n\n".join(s for s in rendered if s).strip()
    if not md:
        md = _fallback_text(response)

    md = _strip_dangling_image_refs(md, image_paths)
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
