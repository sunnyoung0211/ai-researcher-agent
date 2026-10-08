"""审批记录 approvals/<approval_id>.json（详细设计 1 第 3.6 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .common import ApprovalKind, VersionRef

ApprovalStatus = Literal["pending", "approved", "changes_requested", "rejected", "superseded"]
Decision = Literal["approved", "changes_requested", "rejected"]


class Approval(BaseModel):
    approval_id: str  # "ap-0001"，项目内递增
    kind: ApprovalKind
    target: VersionRef
    previous: VersionRef | None = None  # 上一个获批版本，供 GUI 显示差异
    summary: str
    impact: str
    extra_refs: list[VersionRef] = []
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None = None
    decision_comment: str = ""
    decided_by: str | None = None  # "user" | "auto-approve(eval)"
    request_ids: list[str] = []


class ApprovalBrief(BaseModel):
    approval_id: str
    kind: ApprovalKind
    target: VersionRef
    summary: str
    status: ApprovalStatus
    created_at: datetime

    @classmethod
    def of(cls, a: Approval) -> ApprovalBrief:
        return cls(
            approval_id=a.approval_id, kind=a.kind, target=a.target, summary=a.summary,
            status=a.status, created_at=a.created_at,
        )
