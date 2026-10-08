"""文献数据格式（详细设计 2 第 3 节、第 9 节；文献同学起草，主干审核）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PaperRecord(BaseModel):
    paper_id: str  # doi:<doi> > arxiv:<id> > s2:<paperId>
    ids: dict[str, str]
    title: str
    authors: list[str]
    year: int | None
    venue: str | None
    venue_type: Literal["preprint", "conference", "journal", "workshop", "unknown"]
    url: str
    abstract: str | None
    access_level: Literal["full_text", "abstract_only", "metadata_only"]
    fulltext_path: str | None
    source: Literal["semantic_scholar", "arxiv", "manual"]
    snapshot_id: str
    retrieved_at: datetime
    queries: list[str]
    same_as: list[str] = []
    citation_key: str
    bibtex: str
    relevance: dict | None = None
    selected_for_reading: bool = False


class SearchSnapshot(BaseModel):
    snapshot_id: str
    source: str
    query: str
    params: dict
    requested_at: datetime
    status: str
    raw_response: dict | list
    paper_ids: list[str]


class CardItem(BaseModel):
    field: str
    content: str
    kind: Literal["author_claim", "agent_inference"]
    source_loc: str
    quote: str | None
    quote_verified: bool | None = None
    confidence: Literal["high", "medium", "low"]


class ReadingCard(BaseModel):
    card_id: str
    paper_id: str
    role: Literal["methods", "feasibility", "single"]
    based_on: Literal["full_text", "abstract_only"]
    items: list[CardItem]
    relevance_to_question: str
    model: str
    prompt_id: str
    prompt_version: int
    call_id: str
    created_at: datetime


class CitationStatus(BaseModel):
    paper_id: str
    status: Literal["verified", "metadata_only", "not_in_archive", "source_unreachable"]
    citation_key: str | None
    bibtex: str | None
    title: str | None
    url: str | None
    checked_at: datetime
    note: str = ""
