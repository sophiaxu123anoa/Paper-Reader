# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A research paper summarisation agent. Takes a PDF, extracts structured sections (abstract, methods, results, etc.), and returns a JSON summary with key claims and citation anchors.

## Setup

```bash
conda create -n reader-agent python=3.11
conda activate reader-agent
pip install -r requirements.txt
```

Add `ANTHROPIC_API_KEY=...` to `.env`. It is loaded automatically via `python-dotenv`.

## Running each stage independently

```bash
# Stage 1a — extraction (prints first 3 pages)
python extractor.py path/to/paper.pdf

# Stage 1b — classification (prints detected sections and page ranges)
python classifier.py path/to/paper.pdf

# Stage 2 — summariser only (runs full pipeline, prints JSON)
python summarizer.py path/to/paper.pdf

# Full pipeline — CLI
python agent.py path/to/paper.pdf
python agent.py path/to/paper.pdf --out summary.json
python agent.py path/to/paper.pdf --mock   # skip LLM, dummy output

# API server (must be run from inside reader-agent/)
uvicorn api:app --reload
```

Test PDFs live in the parent directory (`../`).

## Architecture: 2-stage hybrid

```
Stage 1 — Extract & Classify (deterministic, no LLM)
  PDF → extractor.py  → list[PageChunk]
                      → classifier.py → list[RawSection]

Stage 2 — Summarise (one LLM call)
  list[RawSection] → summarizer.py → PaperSummary (JSON)
```

Each module is a standalone script and independently testable. The LLM is only invoked in `summarizer.py`. The API streams one SSE event per section so clients receive partial results without waiting for the full paper.

**SSE event sequence:** `status(extracting)` → `status(extracted)` → `status(classified)` → for each section: `status(summarising)` + `section` → `done` (full PaperSummary). On error: `error(message)`.

## File structure

```
extractor.py     # pdfplumber → list[PageChunk], preserves page numbers
classifier.py    # list[PageChunk] → list[RawSection] via regex + LLM fallback
summarizer.py    # summarise() and summarise_section() via native Anthropic tool-use
models.py        # Pydantic schemas shared across all stages
agent.py         # CLI entry point; orchestrates pipeline; --mock flag for testing
api.py           # FastAPI app; POST /summarise streams SSE; ?mock=true for testing
requirements.txt
.env             # ANTHROPIC_API_KEY (never commit)
```

## Pydantic output schema

```
PaperSummary
├── title: str
├── abstract:      SectionSummary | None
├── introduction:  SectionSummary | None
├── methods:       SectionSummary | None
├── results:       SectionSummary | None
├── discussion:    SectionSummary | None
└── conclusion:    SectionSummary | None

SectionSummary
├── summary: str              # 2-3 sentences
└── key_claims: list[KeyClaim]

KeyClaim
├── claim: str                # one sentence
└── citation_anchor: str      # e.g. "Methods, p.4"
```

Internal intermediates:
- `PageChunk(page_num, text)` — extractor output
- `RawSection(name, chunks)` — classifier output; `name` is lowercase canonical ("abstract", "methods", etc.)

## Key implementation details

- **PDF extraction**: `pdfplumber.page.extract_text()` — page numbers are preserved in `PageChunk.page_num` and flow through to `citation_anchor` values.
- **Structured output**: native Anthropic tool-use API — a single tool whose `input_schema` is `Model.model_json_schema()`, with `tool_choice={"type": "tool", "name": "..."}` to force it. Response is validated via `Model.model_validate(tool_block.input)`. No `instructor` dependency.
- **Summariser model**: `claude-sonnet-4-6`. `summarise()` does one call for the full paper (CLI path); `summarise_section()` does one call per section (API streaming path).
- **Section classification**: regex with two strategies — (1) exact fullmatch on short cleaned lines, (2) prefix match with `\b` word boundary for 2-column PDFs where pdfplumber merges headings inline with adjacent-column text. LLM fallback fires only when fewer than 2 sections are detected. Summarisable sections: `abstract`, `introduction`, `methods`, `results`, `discussion`, `conclusion`.
- **Mock mode**: `--mock` (CLI) and `?mock=true` (API) skip all LLM calls and return dummy `SectionSummary` objects built from real extractor/classifier output. Use for pipeline testing without spending API credits. Do not expose `?mock=true` in production — move to an env-var gate before deploying publicly.
- **Package layout known issue**: all modules use bare imports (`from classifier import ...`). The API server must be started from inside `reader-agent/` (`uvicorn api:app`). Running from the parent directory fails with `ModuleNotFoundError`. Fix requires adding `__init__.py` and making the directory a proper package.

## Known scaling weaknesses and migration roadmap

Identified against a target of 1000 concurrent PDF summarisation requests.

### Weakness 1 — Sync CPU work blocks the async event loop

`extract()` and `classify()` are synchronous and called directly inside `async def stream()`. A blocking pdfplumber call holds the entire asyncio event loop, stalling all other coroutines. Fails well before 100 concurrent requests.

| Phase | Change | Effort |
|---|---|---|
| 1 | Wrap CPU-bound calls in `run_in_executor(executor, fn, arg)` | Hours |
| 2 | Move full pipeline to a task queue (Celery + Redis or ARQ); HTTP handler enqueues and returns `job_id` immediately | Days |
| 3 | Autoscale worker tier independently of API tier on queue depth | Weeks |

### Weakness 2 — LLM calls live inside the HTTP request lifecycle

4 sections × 1000 requests = up to 4000 simultaneous Anthropic API calls. Rate limits are hit immediately; each broken call surfaces as a broken SSE stream with no retry. 1000 open HTTP connections held for 30–120 s each is a file-descriptor and memory problem. No backpressure.

| Phase | Change | Effort |
|---|---|---|
| 1 | Parallelise per-section calls with `asyncio.gather` — cuts per-request wall time by ~4× | Hours |
| 2 | Decouple via task queue: `POST /summarise` → `job_id`; `GET /jobs/{id}/stream` → SSE from Redis pub/sub. Connections become short-lived on ingestion side | Days |
| 3 | Rate-limit-aware LLM gateway in workers (token bucket / semaphore); per-job result cache keyed on content hash | Weeks |

### Weakness 3 — No persistent job state; everything lives and dies in-process

PDF bytes are held fully in memory, written to a temp file, processed, and deleted. No record survives a process restart or OOM kill. No deduplication (1000 requests for the same paper run 1000 pipelines). `?mock=true` is a caller-controlled bypass with no access control.

| Phase | Change | Effort |
|---|---|---|
| 1 | Validate and cap uploads at ingestion (size, page count, MIME type); gate mock mode behind an env var | Hours |
| 2 | Store PDFs in object storage (S3/GCS) by content SHA-256; persist job records in Postgres/Redis with status (`queued → processing → done → failed`); add `GET /jobs/{id}` for polling; deduplication becomes a hash lookup before enqueue | Days |
| 3 | Cache completed `PaperSummary` JSON by content hash with TTL; dead-letter queue for jobs that fail after N retries; structured logging with job ID on every line; metrics endpoint (queue depth, LLM latency per section, failure rate) | Weeks |

**Compounding effect**: the sync event-loop issue prevents horizontal scaling from helping; in-request LLM calls make rate-limit events immediately user-visible; lack of job state means there is nothing to fall back on when either of those fail. Natural migration order: `run_in_executor` → task queue → object storage + job store → rate-limit-aware LLM gateway.
