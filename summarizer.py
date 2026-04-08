"""Stage 2 — Summarisation.

Sends classified sections to Claude and returns a validated PaperSummary.
One LLM call per paper; section text and page metadata are passed as structured input
so the model can produce accurate citation anchors.
"""

from __future__ import annotations

import anthropic
from dotenv import load_dotenv

from models import PaperSummary, RawSection, SectionSummary

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a precise research paper summarisation assistant. "
    "Summarise only what the text states — do not infer or embellish. "
    "For every key claim, set citation_anchor to the section name and page "
    "range shown in the section header (e.g. 'Methods, p.4' or 'Results, pp.6-7')."
)


def _format_sections(sections: list[RawSection]) -> str:
    """Render sections as labelled blocks with page metadata for the LLM."""
    parts = []
    for s in sections:
        start, end = s.page_range
        page_label = f"p.{start}" if start == end else f"pp.{start}-{end}"
        header = f"=== {s.name.upper()} ({page_label}) ==="
        parts.append(f"{header}\n{s.full_text}")
    return "\n\n".join(parts)


def summarise(sections: list[RawSection]) -> PaperSummary:
    """Summarise a list of RawSections into a structured PaperSummary.

    Uses the Anthropic tool-use API with tool_choice forced to a single tool
    whose input_schema is derived from PaperSummary. This guarantees a
    structured response without any third-party wrapper library.
    """
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": "paper_summary",
                "description": "Structured summary of a research paper.",
                "input_schema": PaperSummary.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": "paper_summary"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarise each section below. Extract the paper title from the content.\n\n"
                    + _format_sections(sections)
                ),
            }
        ],
    )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    return PaperSummary.model_validate(tool_block.input)


def summarise_section(section: RawSection) -> SectionSummary:
    """Summarise a single section. Used by the streaming API for per-section SSE events."""
    client = anthropic.Anthropic()

    start, end = section.page_range
    page_label = f"p.{start}" if start == end else f"pp.{start}-{end}"

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": "section_summary",
                "description": "Structured summary of one paper section.",
                "input_schema": SectionSummary.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": "section_summary"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Summarise the {section.name.upper()} section ({page_label}) below. "
                    "For each key claim use a citation_anchor referencing the section "
                    f"name and page label shown, e.g. '{section.name.title()}, {page_label}'.\n\n"
                    f"=== {section.name.upper()} ({page_label}) ===\n{section.full_text}"
                ),
            }
        ],
    )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    return SectionSummary.model_validate(tool_block.input)


if __name__ == "__main__":
    import json
    import sys

    from classifier import classify
    from extractor import extract

    path = sys.argv[1] if len(sys.argv) > 1 else "../1907.12412v2.pdf"
    chunks = extract(path)
    sections = classify(chunks)
    print(f"Summarising {len(sections)} sections: {[s.name for s in sections]}")
    summary = summarise(sections)
    print(json.dumps(summary.model_dump(exclude_none=True), indent=2))
