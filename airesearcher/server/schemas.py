"""API 的请求与响应结构（详细设计 1 第 9 节）。FastAPI 自动生成的 /docs 页面是权威说明。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from airesearcher.core.models.approval import Approval, ApprovalBrief
from airesearcher.core.models.budget import BudgetStatus
from airesearcher.core.models.common import ProjectState
from airesearcher.core.models.question import Question
from airesearcher.core.models.run import RunSummary


class TaskInfo(BaseModel):
    name: str
    path: str
    domain: str
    description: str


class ProjectSummary(BaseModel):
    project_id: str
    title: str
    state: ProjectState
    stage_label: str
    pending_approvals: int
    has_question: bool
    created_at: datetime
    updated_at: datetime | None = None
    root: str


class ProjectStatus(BaseModel):
    project_id: str
    title: str
    state: ProjectState
    stage: str | None
    stage_label: str
    progress: dict
    blocking_reason: str | None
    pending_approvals: list[ApprovalBrief]
    pending_question: Question | None
    active_runs: list[RunSummary]
    budget: BudgetStatus
    next_actions: list[str]
    updated_at: datetime
    root: str = ""


class CreateProject(BaseModel):
    title: str | None = None
    idea_text: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    task: str = "tasks/smoke"
    budget: dict[str, dict] | None = None
    evidence_check: bool = True
    dev: dict[str, Any] | None = None  # 开发用：假实现开关与参数（project.yaml 的 dev）
    request_id: str = ""


class ActionRequest(BaseModel):
    action: Literal["pause", "resume", "cancel", "reopen"]
    request_id: str = ""


class AnswerRequest(BaseModel):
    question_id: str
    choice: str | None = None
    text: str = ""
    request_id: str = ""


class DecisionRequest(BaseModel):
    decision: Literal["approved", "changes_requested", "rejected"]
    comment: str = ""
    expected_sha256: str
    request_id: str = ""


class ExtraRef(BaseModel):
    artifact_id: str
    version: int
    sha256: str
    title: str
    url: str
    content: str | None = None  # 小的文本产物直接附上内容（CLI 用）


class ApprovalDetail(BaseModel):
    approval: Approval
    target_content: str | None
    previous_content: str | None
    target_path: str
    extra: list[ExtraRef]


class EditRequest(BaseModel):
    artifact_id: str
    content: str
    base_sha256: str
    request_id: str = ""


class LabelRequest(BaseModel):
    label: Literal["trusted", "suspicious", "invalid"]
    reason: str = ""
    request_id: str = ""


class RequestIdOnly(BaseModel):
    request_id: str = ""


class BudgetPatch(BaseModel):
    model_config = {"extra": "allow"}
    request_id: str = ""
