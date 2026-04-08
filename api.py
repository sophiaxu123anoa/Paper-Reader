"""FastAPI endpoint — POST /summarise

Accepts a PDF upload and streams a JSON summary via Server-Sent Events.
Each section is summarised independently and emitted as it completes,
so clients receive partial results without waiting for the full paper.

SSE event types:
    status   {"stage": "extracting"|"extracted"|"classified"|"summarising", ...}
    section  {"name": str, "summary": str, "key_claims": [...]}
    done     full PaperSummary JSON (title + all sections)
    error    {"message": str}

Query params:
    mock=true   Skip LLM calls; return dummy data for pipeline testing.

Run with:
    uvicorn api:app --reload
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, Query, UploadFile
from fastapi.responses import StreamingResponse

from classifier import classify
from extractor import extract
from models import KeyClaim, PaperSummary, RawSection, SectionSummary
from summarizer import summarise_section

app = FastAPI()


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _title_heuristic(chunks) -> str:
    """Best-effort title: first substantial line of page 1 that isn't metadata."""
    if not chunks:
        return "Unknown"
    for line in chunks[0].text.splitlines():
        line = line.strip()
        if len(line) > 20 and "@" not in line and not line.startswith("{"):
            return line
    return "Unknown"


def _mock_section(section: RawSection) -> SectionSummary:
    start, end = section.page_range
    anchor = (
        f"{section.name.title()}, p.{start}"
        if start == end
        else f"{section.name.title()}, pp.{start}-{end}"
    )
    return SectionSummary(
        summary=(
            f"[MOCK] {section.name.title()} — "
            f"{len(section.chunks)} page(s), {len(section.full_text):,} chars."
        ),
        key_claims=[
            KeyClaim(
                claim=f"[MOCK] Claim from the {section.name} section.",
                citation_anchor=anchor,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/summarise")
async def summarise_pdf(
    file: UploadFile,
    mock: bool = Query(default=False, description="Skip LLM calls for testing"),
):
    async def stream():
        content = await file.read()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        try:
            yield _sse("status", {"stage": "extracting"})
            chunks = extract(tmp_path)
            yield _sse("status", {"stage": "extracted", "pages": len(chunks)})

            sections = classify(chunks)
            yield _sse("status", {
                "stage": "classified",
                "sections": [s.name for s in sections],
            })

            title = "[MOCK] Paper title placeholder" if mock else _title_heuristic(chunks)
            valid_fields = set(PaperSummary.model_fields) - {"title"}
            section_summaries: dict[str, SectionSummary] = {}

            for section in sections:
                yield _sse("status", {"stage": "summarising", "section": section.name})
                summary = _mock_section(section) if mock else summarise_section(section)
                section_summaries[section.name] = summary
                yield _sse("section", {"name": section.name, **summary.model_dump()})

            paper = PaperSummary(
                title=title,
                **{k: v for k, v in section_summaries.items() if k in valid_fields},
            )
            yield _sse("done", paper.model_dump(exclude_none=True))

        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
        finally:
            tmp_path.unlink(missing_ok=True)

    return StreamingResponse(stream(), media_type="text/event-stream")
