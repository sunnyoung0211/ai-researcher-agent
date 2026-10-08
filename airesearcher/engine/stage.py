"""Stage 协议、StageContext、StepResult（详细设计 1 第 5.1 节）。所有阶段 Agent 都要实现 Stage。

阶段每次 step() 做一小步，返回下面六种结果之一：

    Continue(note)                    有进展，马上再调我
    Wait(reason, seconds)             在等外部事情（如运行结束），过一会再调我
    NeedsApproval(target, kind, ...)  提交审批，引擎转入对应 *Pending
    Advance(to, reason, refs)         请求状态转换，引擎按转换表校验
    Stop(reason, question)            需要用户决定，引擎转 Paused；带 question 时等待回答
    Error(message, retryable)         出错
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from airesearcher.core.models.approval import Approval
from airesearcher.core.models.common import ApprovalKind, ProjectState, VersionRef
from airesearcher.core.models.project import ProjectConfig
from airesearcher.core.models.question import Answer, Question

if TYPE_CHECKING:
    from airesearcher.core.approvals import Approvals
    from airesearcher.core.archive import Archive
    from airesearcher.core.budget import Budget
    from airesearcher.core.events import Events
    from airesearcher.core.permissions import Permissions
    from airesearcher.core.questions import Questions
    from airesearcher.core.workspace import Workspace
    from airesearcher.llm.gateway import LLMGateway
    from airesearcher.runtime.executor import Executor
    from airesearcher.skills.loader import SkillLoader


# ---------------- StepResult ----------------


@dataclass
class Continue:
    note: str = ""


@dataclass
class Wait:
    reason: str
    seconds: float = 10


@dataclass
class NeedsApproval:
    target: VersionRef
    kind: ApprovalKind
    summary: str
    impact: str
    extra_refs: list[VersionRef] = field(default_factory=list)


@dataclass
class Advance:
    to: ProjectState
    reason: str
    refs: list[VersionRef] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"to": ProjectState(self.to).value, "reason": self.reason,
                "refs": [r.model_dump(mode="json") for r in self.refs]}

    @classmethod
    def from_json(cls, d: dict) -> Advance:
        return cls(to=ProjectState(d["to"]), reason=d["reason"],
                   refs=[VersionRef.model_validate(r) for r in d.get("refs", [])])


@dataclass
class Stop:
    reason: str
    question: Question | None = None


@dataclass
class Error:
    message: str
    retryable: bool = True


StepResult = Continue | Wait | NeedsApproval | Advance | Stop | Error


def describe(result: StepResult) -> str:
    """给人看的一行说明（air dev run-stage 打印用）。"""
    match result:
        case Continue(note=n):
            return f"Continue({n})"
        case Wait(reason=r, seconds=s):
            return f"Wait({r}, {s}s)"
        case NeedsApproval(target=t, kind=k, summary=s):
            return f"NeedsApproval({k}: {t.artifact_id} v{t.version} — {s})"
        case Advance(to=to, reason=r):
            return f"Advance(→ {ProjectState(to).value}: {r})"
        case Stop(reason=r, question=q):
            return f"Stop({r}" + (f"; 问题：{q.text}" if q else "") + ")"
        case Error(message=m, retryable=rt):
            return f"Error({m}, retryable={rt})"
    return repr(result)


# ---------------- StageContext ----------------


@dataclass
class StageContext:
    project: ProjectConfig
    root: Path
    state: ProjectState
    archive: Archive
    events: Events
    approvals: Approvals
    questions: Questions
    budget: Budget
    permissions: Permissions
    workspace: Workspace
    llm: LLMGateway
    skills: SkillLoader
    executor: Executor | None
    scratch: dict[str, Any]
    approved: dict[str, VersionRef]
    feedback: list[Approval]
    entry: Advance | None
    stale: list[str]
    answer: Answer | None
    log: Callable[[str], None]

    def progress(self, label: str, done: int | None = None, total: int | None = None, unit: str = "") -> None:
        """上报给 GUI / CLI 的进度：写入 scratch["_label"] 和 scratch["_progress"]。"""
        self.scratch["_label"] = label
        if total is not None:
            self.scratch["_progress"] = {"done": done or 0, "total": total, "unit": unit}
        else:
            self.scratch.pop("_progress", None)


@runtime_checkable
class Stage(Protocol):
    name: str

    def step(self, ctx: StageContext) -> StepResult: ...


def call_background(stage: Any, ctx: StageContext) -> None:
    """可选的 background(ctx)：项目处于等待状态且有活动运行时调用（5.2 附加规则）。"""
    fn = getattr(stage, "background", None)
    if callable(fn):
        fn(ctx)
