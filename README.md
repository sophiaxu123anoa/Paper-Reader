# reader-agent

A research paper summarisation agent. Upload a PDF, get back a structured JSON summary with key claims and citation anchors — section by section.

## How it works

Two-stage pipeline, no pre-processing:

```
Stage 1 — Extract & Classify (no LLM)
  PDF → pdfplumber → list[PageChunk]
                   → regex classifier → list[RawSection]

Stage 2 — Summarise (one LLM call per section)
  list[RawSection] → Claude claude-sonnet-4-6 → PaperSummary (JSON)
```

The classifier uses regex heading detection with a word-boundary prefix match for 2-column PDFs (where pdfplumber merges section headings inline with adjacent column text). It falls back to a Claude call only when fewer than 2 sections are detected.

## Setup

```bash
conda create -n reader-agent python=3.11
conda activate reader-agent
pip install -r requirements.txt
cp .env.example .env   # then add your key
```

`.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### CLI

```bash
# Full pipeline — prints JSON to stdout
python agent.py path/to/paper.pdf

# Write to file
python agent.py path/to/paper.pdf --out summary.json

# Skip LLM calls (test the pipeline without spending API credits)
python agent.py path/to/paper.pdf --mock
```

### API

```bash
# Run from inside reader-agent/
uvicorn api:app --reload
```

`POST /summarise` — accepts a PDF upload, streams the summary back as [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events).

```bash
curl -N -X POST "http://localhost:8000/summarise" \
  -F "file=@paper.pdf"

# Without spending API credits
curl -N -X POST "http://localhost:8000/summarise?mock=true" \
  -F "file=@paper.pdf"
```

#### SSE event stream

Each section is summarised independently and emitted as it completes:

```
event: status
data: {"stage": "extracting"}

event: status
data: {"stage": "extracted", "pages": 8}

event: status
data: {"stage": "classified", "sections": ["abstract", "introduction", "results", "conclusion"]}

event: status
data: {"stage": "summarising", "section": "abstract"}

event: section
data: {"name": "abstract", "summary": "...", "key_claims": [...]}

... (one status + section pair per detected section)

event: done
data: {"title": "...", "abstract": {...}, "introduction": {...}, ...}

event: error   ← only on failure
data: {"message": "..."}
```

## Output schema

```json
{
  "title": "ERNIE 2.0: A Continual Pre-Training Framework ...",
  "abstract": {
    "summary": "The authors propose ERNIE 2.0 ...",
    "key_claims": [
      {
        "claim": "ERNIE 2.0 outperforms BERT and XLNet on 16 tasks.",
        "citation_anchor": "Abstract, p.1"
      }
    ]
  },
  "introduction": { ... },
  "results":      { ... },
  "conclusion":   { ... }
}
```

Detected sections: `abstract`, `introduction`, `methods`, `results`, `discussion`, `conclusion`. Fields are omitted (not null) when a section is not found in the paper.

## Running individual stages

Each module is independently runnable for debugging:

```bash
python extractor.py paper.pdf      # prints first 3 pages of extracted text
python classifier.py paper.pdf     # prints detected sections and page ranges
python summarizer.py paper.pdf     # runs full pipeline, prints JSON
```

## Known limitations

- **2-column layout**: pdfplumber's `extract_text()` merges columns; section headings may be missed if the paper uses non-standard heading styles. The prefix-match heuristic covers most AAAI/ACL/NeurIPS formats.
- **Non-standard section names**: papers that use domain-specific headings ("ERNIE 2.0 Model", "Pre-training Tasks") won't produce a `methods` section — that content appears under `introduction`.
- **Package imports**: all modules use bare imports and must be run from inside `reader-agent/`. The API server (`uvicorn api:app`) must also be started from that directory.
- **`?mock=true` is not access-controlled**: suitable for local development only. Gate it behind an environment variable before any public deployment.
