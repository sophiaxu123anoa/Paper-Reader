"""Full pipeline orchestrator.

Usage:
    python agent.py path/to/paper.pdf
    python agent.py path/to/paper.pdf --out summary.json
    python agent.py path/to/paper.pdf --mock        # skip LLM, return dummy summary
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from classifier import classify
from extractor import extract
from models import KeyClaim, PaperSummary, RawSection, SectionSummary
from summarizer import summarise


def _mock_summarise(sections: list[RawSection]) -> PaperSummary:
    """Build a dummy PaperSummary from real detected sections without an LLM call.

    Uses actual section names, page ranges, and char counts so the output
    reflects what the extractor/classifier produced.
    """
    valid_fields = set(PaperSummary.model_fields) - {"title"}
    section_summaries: dict[str, SectionSummary] = {}

    for s in sections:
        if s.name not in valid_fields:
            continue
        start, end = s.page_range
        anchor = (
            f"{s.name.title()}, p.{start}"
            if start == end
            else f"{s.name.title()}, pp.{start}-{end}"
        )
        section_summaries[s.name] = SectionSummary(
            summary=(
                f"[MOCK] {s.name.title()} section — "
                f"{len(s.chunks)} page(s), {len(s.full_text):,} chars extracted."
            ),
            key_claims=[
                KeyClaim(
                    claim=f"[MOCK] Representative claim from the {s.name} section.",
                    citation_anchor=anchor,
                )
            ],
        )

    return PaperSummary(title="[MOCK] Paper title placeholder", **section_summaries)


def run(pdf_path: str | Path, mock: bool = False) -> dict:
    chunks = extract(pdf_path)
    sections = classify(chunks)
    summary = _mock_summarise(sections) if mock else summarise(sections)
    return summary.model_dump(exclude_none=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarise a research paper PDF.")
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--out", help="Write JSON output to this file instead of stdout")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Skip the LLM call and return a dummy summary (for pipeline testing)",
    )
    args = parser.parse_args()

    result = run(args.pdf, mock=args.mock)
    output = json.dumps(result, indent=2)

    if args.out:
        Path(args.out).write_text(output)
        print(f"Summary written to {args.out}")
    else:
        print(output)
