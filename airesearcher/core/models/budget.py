"""预算流水与状态（详细设计 1 第 4.3 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

BudgetLevel = Literal["ok", "warn", "exhausted"]


class BudgetEntry(BaseModel):
    ts: datetime
    category: str  # llm_usd | llm_tokens | llm_calls | wall_hours | cpu_hours | gpu_hours | storage_gb
    amount: float
    source: str
    data: dict = {}


class BudgetCategory(BaseModel):
    used: float = 0.0
    soft: float | None = None
    hard: float | None = None
    level: BudgetLevel = "ok"


class BudgetStatus(BaseModel):
    categories: dict[str, BudgetCategory]
    exhausted: bool = False  # 任何一类 exhausted
    warn: bool = False

    def exhausted_categories(self) -> list[str]:
        return [k for k, v in self.categories.items() if v.level == "exhausted"]
