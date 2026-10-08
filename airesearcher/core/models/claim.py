"""论断-证据矩阵、核验报告、编译报告（详细设计 4 第 3.2~3.4 节；论文同学起草）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .common import VersionRef

Stat = Literal["mean", "std", "ci95_low", "ci95_high", "n", "min", "max", "diff_mean", "p_value"]
Support = Literal["supported", "partially_supported", "unsupported", "contradicted", "unverifiable"]


class ValueRef(BaseModel):
    key: str  # 宏键，如 "C1.lora.500.accuracy.mean"
    aggregate_id: str
    aggregate_version: int
    group: dict
    metric: str
    stat: Stat
    contrast: dict | None = None
    fmt: dict = {"scale": 100, "decimals": 1, "suffix": ""}
    rendered: str


class EvidenceLink(BaseModel):
    type: Literal["aggregate", "figure", "run", "paper", "plan", "log"]
    ref: str
    version: int | None = None
    selector: dict | None = None
    note: str = ""


class Comparison(BaseModel):
    a: dict
    b: dict
    metric: str
    asserted: Literal["a>b", "a<b", "a≈b"]


class Claim(BaseModel):
    claim_id: str
    section: Literal[
        "abstract", "introduction", "related_work", "method", "setup", "results", "discussion", "conclusion"
    ]
    text: str
    type: Literal["numeric", "comparative", "qualitative", "citation", "method_fact"]
    statement_kind: Literal["observation", "interpretation"]
    hedge: Literal["none", "likely", "possible"]
    importance: Literal["major", "minor"]
    hypothesis: str | None = None
    values: list[ValueRef] = []
    comparison: Comparison | None = None
    evidence: list[EvidenceLink]
    location: dict | None = None


class Issue(BaseModel):
    code: str
    severity: Literal["blocker", "major", "minor"]
    claim_id: str | None = None
    location: dict = {}
    sentence: str = ""
    detail: str = ""
    suggestion: str = ""


class ClaimCheck(BaseModel):
    claim_id: str
    support: Support
    issues: list[Issue] = []
    recomputed: dict[str, str] = {}


class CheckReport(BaseModel):
    paper_ref: VersionRef
    mode: Literal["full", "audit"]
    claims: list[ClaimCheck]
    unbound_issues: list[Issue] = []
    counts: dict = {}
    compile_ok: bool
    created_at: datetime


class CompileReport(BaseModel):
    ok: bool
    engine: str
    entry: str
    errors: list[dict] = []
    undefined_citations: list[str] = []
    undefined_refs: list[str] = []
    missing_files: list[str] = []
    overfull_boxes: int = 0
    pages: int | None = None
    log_path: str
    seconds: float
