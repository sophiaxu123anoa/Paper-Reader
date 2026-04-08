from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PageChunk(BaseModel):
    """A single page of text extracted from a PDF."""

    page_num: int
    text: str


class RawSection(BaseModel):
    """A classified section produced by classifier.py."""

    name: str  # canonical lowercase: "abstract", "introduction", "methods", "results", "discussion", "conclusion"
    chunks: list[PageChunk]

    @property
    def full_text(self) -> str:
        return "\n\n".join(chunk.text for chunk in self.chunks)

    @property
    def page_range(self) -> tuple[int, int]:
        pages = [c.page_num for c in self.chunks]
        return min(pages), max(pages)


class KeyClaim(BaseModel):
    """A single key claim with its source location."""

    claim: str = Field(description="A single key claim or finding, one sentence")
    citation_anchor: str = Field(
        description="Source location in the paper, e.g. 'Methods, p.4' or 'Results, pp.6-7'"
    )


class SectionSummary(BaseModel):
    """Summary and key claims for one section."""

    summary: str = Field(description="2-3 sentence summary of the section")
    key_claims: list[KeyClaim] = Field(description="3-5 key claims or findings from this section")


class PaperSummary(BaseModel):
    """Full structured summary of a research paper — final pipeline output."""

    title: str
    abstract: Optional[SectionSummary] = None
    introduction: Optional[SectionSummary] = None
    methods: Optional[SectionSummary] = None
    results: Optional[SectionSummary] = None
    discussion: Optional[SectionSummary] = None
    conclusion: Optional[SectionSummary] = None
