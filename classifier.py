"""Stage 1b — Section classification.

Assigns PageChunks to named sections using regex heading detection.
Falls back to an LLM call if fewer than 2 sections are detected.
"""

from __future__ import annotations

import re

from models import PageChunk, RawSection

# Patterns are matched against the full cleaned heading text (after stripping numbers).
# Use re.fullmatch so "results" in a sentence body cannot match.
SECTION_PATTERNS: dict[str, list[str]] = {
    "abstract": [r"abstract"],
    "introduction": [r"introduction"],
    "methods": [r"methods?|methodology|materials?\s+and\s+methods?|experimental\s+setup"],
    "results": [r"results?|experiments?|experimental\s+results?"],
    "discussion": [r"discussion"],
    "conclusion": [r"conclusions?|concluding\s+remarks?|summary"],
    "references": [r"references?|bibliography"],
}

SUMMARISABLE = {"abstract", "introduction", "methods", "results", "discussion", "conclusion"}


def _strip_section_number(line: str) -> str:
    """Remove leading section numbers: '1.', '2.1', 'A.', '1 '."""
    text = re.sub(r"^[A-Z\d]+(?:\.\d+)*\.?\s+", "", line.strip())
    return text.strip()


def _match_canonical(text: str) -> str | None:
    """Return canonical section name if text fully matches a known heading pattern."""
    lower = text.lower().strip()
    for name, patterns in SECTION_PATTERNS.items():
        for pat in patterns:
            if re.fullmatch(pat, lower):
                return name
    return None


def _detect_boundaries(chunks: list[PageChunk]) -> list[tuple[int, str]]:
    """Scan pages line-by-line for section headings.

    Returns a list of (page_num, canonical_section_name), in page order,
    with each canonical name appearing at most once.

    Two detection strategies per line:
    - Exact: the entire cleaned line matches a section pattern (standard layout).
    - Prefix: the line starts with a section name followed by whitespace (2-column
      layout where the heading is merged with adjacent-column text by pdfplumber).
    """
    boundaries: list[tuple[int, str]] = []
    seen: set[str] = set()

    for chunk in chunks:
        for line in chunk.text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # --- Exact match: cleaned line is short and IS a heading ---
            if len(stripped) <= 80:
                cleaned = _strip_section_number(stripped)
                if cleaned and len(cleaned) <= 60:
                    canonical = _match_canonical(cleaned)
                    if canonical and canonical not in seen:
                        boundaries.append((chunk.page_num, canonical))
                        seen.add(canonical)
                        continue

            # --- Prefix match: line starts with a section name as a complete word ---
            # Handles 2-column papers where pdfplumber merges headings inline with
            # adjacent-column text (e.g. "Abstract sentence proximity...").
            # \b after the pattern prevents matching concatenated words like
            # "resultsinvariouslanguageunderstanding..." against "results?".
            for name, patterns in SECTION_PATTERNS.items():
                if name in seen:
                    continue
                for pat in patterns:
                    if re.match(r"(?:" + pat + r")\b", stripped.lower()):
                        boundaries.append((chunk.page_num, name))
                        seen.add(name)
                        break

    return boundaries


def _boundaries_to_sections(
    chunks: list[PageChunk],
    boundaries: list[tuple[int, str]],
) -> list[RawSection]:
    """Group PageChunks into RawSections using detected section start pages.

    Each page is assigned to the section whose start page is closest before it.
    Only sections in SUMMARISABLE are returned.
    """
    if not boundaries:
        return []

    boundaries = sorted(boundaries, key=lambda x: x[0])
    all_pages = sorted(c.page_num for c in chunks)
    chunk_by_page = {c.page_num: c for c in chunks}

    sections: list[RawSection] = []
    for i, (start, name) in enumerate(boundaries):
        raw_end = boundaries[i + 1][0] - 1 if i + 1 < len(boundaries) else max(all_pages)
        # Clamp: if the next section starts on the same page, this section still
        # owns its start page (avoids empty range when two headings share a page).
        end = max(start, raw_end)
        section_chunks = [
            chunk_by_page[p] for p in range(start, end + 1) if p in chunk_by_page
        ]
        if section_chunks and name in SUMMARISABLE:
            sections.append(RawSection(name=name, chunks=section_chunks))

    return sections


def _classify_with_llm(chunks: list[PageChunk]) -> list[RawSection]:
    """LLM fallback: ask Claude to identify section boundaries in the first 8 pages."""
    import anthropic
    from dotenv import load_dotenv
    from pydantic import BaseModel

    load_dotenv()

    class Boundary(BaseModel):
        page_num: int
        section_name: str  # canonical lowercase

    class BoundaryList(BaseModel):
        boundaries: list[Boundary]

    sample = chunks[:8]
    page_text = "\n\n---PAGE BREAK---\n\n".join(
        f"[Page {c.page_num}]\n{c.text[:1000]}" for c in sample
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        tools=[
            {
                "name": "section_boundaries",
                "description": "Section boundaries detected in the paper.",
                "input_schema": BoundaryList.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": "section_boundaries"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Identify section boundaries in this academic paper excerpt. "
                    "Return the page number where each section starts and its canonical name. "
                    "Use only: abstract, introduction, methods, results, "
                    "discussion, conclusion, references.\n\n" + page_text
                ),
            }
        ],
    )

    tool_block = next(b for b in response.content if b.type == "tool_use")
    result = BoundaryList.model_validate(tool_block.input)
    boundaries = [(b.page_num, b.section_name) for b in result.boundaries]
    return _boundaries_to_sections(chunks, boundaries)


def classify(chunks: list[PageChunk]) -> list[RawSection]:
    """Return summarisable RawSections from a list of PageChunks.

    Uses regex heading detection; falls back to an LLM call if fewer than
    2 sections are found.
    """
    boundaries = _detect_boundaries(chunks)
    if len(boundaries) >= 2:
        return _boundaries_to_sections(chunks, boundaries)
    return _classify_with_llm(chunks)


if __name__ == "__main__":
    import sys

    from extractor import extract

    path = sys.argv[1] if len(sys.argv) > 1 else "../1907.12412v2.pdf"
    chunks = extract(path)
    sections = classify(chunks)
    print(f"Detected {len(sections)} summarisable sections:")
    for s in sections:
        start, end = s.page_range
        print(f"  {s.name}: pp.{start}-{end} ({len(s.chunks)} pages, {len(s.full_text)} chars)")
