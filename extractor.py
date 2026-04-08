"""Stage 1a — PDF extraction.

Converts a PDF into a list of PageChunk objects, one per non-blank page,
preserving page numbers for downstream citation anchoring.
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from models import PageChunk


def extract(pdf_path: str | Path) -> list[PageChunk]:
    """Return one PageChunk per non-blank page in the PDF."""
    chunks: list[PageChunk] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append(PageChunk(page_num=i, text=text))
    return chunks


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "../1907.12412v2.pdf"
    chunks = extract(path)
    print(f"Extracted {len(chunks)} pages from {path}")
    for chunk in chunks[:3]:
        print(f"\n--- Page {chunk.page_num} ({len(chunk.text)} chars) ---")
        print(chunk.text[:400])
