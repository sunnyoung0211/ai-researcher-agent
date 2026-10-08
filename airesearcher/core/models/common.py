"""通用类型（详细设计 1 第 3.1 节）。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class VersionRef(BaseModel):
    artifact_id: str  # 即工作区内的相对路径，如 "idea/selected.md"
    version: int  # 从 1 开始递增
    sha256: str  # 该版本内容的哈希（目录型产物为清单哈希）

    def short(self) -> str:
        return f"{self.artifact_id}@v{self.version}"


class ProjectState(str, Enum):
    IdeaDrafting = "IdeaDrafting"
    IdeaPending = "IdeaPending"
    PlanDrafting = "PlanDrafting"
    PlanPending = "PlanPending"
    Executing = "Executing"
    Analyzing = "Analyzing"
    LogPending = "LogPending"
    Writing = "Writing"
    ManuscriptPending = "ManuscriptPending"
    Completed = "Completed"
    Paused = "Paused"
    BudgetExhausted = "BudgetExhausted"
    Failed = "Failed"


ArtifactKind = Literal[
    "project", "idea", "literature", "reading_card", "search_snapshot", "scores", "gap_table",
    "plan", "precheck", "code", "config", "run", "aggregate", "process_log",
    "figure", "template", "paper", "claims", "review_report", "other",
]
ApprovalKind = Literal["idea", "plan", "log", "manuscript"]
Producer = str  # "agent:<stage>/<role>" | "human-edited" | "system"

APPROVAL_KINDS: tuple[str, ...] = ("idea", "plan", "log", "manuscript")
