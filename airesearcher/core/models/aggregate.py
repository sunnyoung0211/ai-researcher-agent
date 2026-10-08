"""汇总结果 artifacts/aggregates/<aggregate_id>.json（详细设计 3 第 8.2 节；实验同学起草）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .common import VersionRef


class AggregateRow(BaseModel):
    group: dict[str, Any]
    metric: str
    n: int
    mean: float
    std: float | None
    sem: float | None
    ci95: list[float] | None
    min: float
    max: float
    values: list[float]
    run_ids: list[str]
    outliers: list[float] = []


class Contrast(BaseModel):
    a: dict[str, Any]
    b: dict[str, Any]
    metric: str
    diff_mean: float
    welch_t: float | None
    p_value: float | None
    n_a: int
    n_b: int


class Aggregate(BaseModel):
    aggregate_id: str
    comparison: dict[str, Any]
    plan_ref: VersionRef
    script: dict[str, Any]
    eval_condition: str
    rows: list[AggregateRow]
    contrasts: list[Contrast] = []
    excluded: list[dict[str, Any]] = []
    cost: dict[str, Any] = {}
    created_at: datetime
