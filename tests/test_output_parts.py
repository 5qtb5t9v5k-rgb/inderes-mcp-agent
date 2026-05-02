"""Tests for `extract_parts` — turning MAF response objects into structured markdown."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from inderes_agent.observability.output_parts import extract_parts


# ---------------------------------------------------------------------------
# Fakes mimicking the shape of MAF Content / Message / AgentResponse
# ---------------------------------------------------------------------------

@dataclass
class FakeContent:
    type: str
    text: str | None = None
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    media_type: str | None = None
    uri: str | None = None


@dataclass
class FakeMessage:
    contents: list[FakeContent]


@dataclass
class FakeResponse:
    messages: list[FakeMessage]
    text: str = ""


def _data_uri(media_type: str, payload: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(payload).decode()}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_text_only(tmp_path):
    response = FakeResponse(messages=[FakeMessage(contents=[
        FakeContent(type="text", text="Hello world."),
    ])])
    md, images = extract_parts(response, run_dir=tmp_path, agent_label="quant")
    assert md == "Hello world."
    assert images == []


def test_code_block_fenced(tmp_path):
    code = "x = 1\nprint(x)"
    response = FakeResponse(messages=[FakeMessage(contents=[
        FakeContent(type="text", text="Computing:"),
        FakeContent(
            type="code_interpreter_tool_call",
            inputs=[FakeContent(type="text", text=code)],
        ),
        FakeContent(
            type="code_interpreter_tool_result",
            outputs=[FakeContent(type="text", text="1\n")],
        ),
        FakeContent(type="text", text="Done."),
    ])])
    md, images = extract_parts(response, run_dir=tmp_path, agent_label="quant")
    assert "```python\nx = 1\nprint(x)\n```" in md
    assert "```\n1\n```" in md
    assert images == []


def test_image_extraction(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\nfakepayload"
    response = FakeResponse(messages=[FakeMessage(contents=[
        FakeContent(
            type="code_interpreter_tool_result",
            outputs=[
                FakeContent(type="text", text="plot saved"),
                FakeContent(
                    type="data",
                    media_type="image/png",
                    uri=_data_uri("image/png", fake_png),
                ),
            ],
        ),
    ])])
    md, images = extract_parts(response, run_dir=tmp_path, agent_label="quant")
    assert images == ["images/quant-1.png"]
    assert "![chart](images/quant-1.png)" in md
    assert (tmp_path / "images" / "quant-1.png").read_bytes() == fake_png


def test_function_calls_skipped(tmp_path):
    """MCP function-call/result parts shouldn't appear in the rendered output."""
    response = FakeResponse(messages=[FakeMessage(contents=[
        FakeContent(type="function_call", text=None),
        FakeContent(type="function_result", text="this should not appear"),
        FakeContent(type="text", text="Final answer."),
    ])])
    md, _ = extract_parts(response, run_dir=tmp_path, agent_label="quant")
    assert md == "Final answer."
    assert "should not appear" not in md


def test_label_safe_for_filenames(tmp_path):
    """Agent labels with slashes / spaces become safe filename fragments."""
    fake_png = b"\x89PNG"
    response = FakeResponse(messages=[FakeMessage(contents=[
        FakeContent(
            type="code_interpreter_tool_result",
            outputs=[FakeContent(type="data", media_type="image/png", uri=_data_uri("image/png", fake_png))],
        ),
    ])])
    md, images = extract_parts(response, run_dir=tmp_path, agent_label="quant — Sampo / Inc")
    # In the filename portion (after the leading "images/"), no emdash / space allowed.
    # Forward-slash will appear because of the "images/" prefix — that's intentional.
    assert images, "image should have been extracted"
    filename = images[0].rsplit("/", 1)[1]
    for ch in "— ":
        assert ch not in filename, f"unsafe char {ch!r} in {filename!r}"


def test_fallback_when_no_messages(tmp_path):
    """If response shape is unexpected, fall back to .text."""
    @dataclass
    class WeirdResponse:
        text: str = "fallback content"

    md, images = extract_parts(WeirdResponse(), run_dir=tmp_path, agent_label="quant")
    assert md == "fallback content"
    assert images == []


def test_empty_response_returns_empty_text(tmp_path):
    """No content + no .text → empty string, not crash."""
    @dataclass
    class EmptyResponse:
        messages: list = field(default_factory=list)
        text: str = ""

    md, _ = extract_parts(EmptyResponse(), run_dir=tmp_path, agent_label="quant")
    assert md == ""
