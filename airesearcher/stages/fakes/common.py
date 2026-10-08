"""假实现共用的小工具。"""

from __future__ import annotations

from pathlib import Path

import yaml

from airesearcher.core.models.approval import Approval
from airesearcher.core.models.plan import TaskConfig
from airesearcher.core.workspace import repo_root, resolve_task_dir
from airesearcher.engine.stage import StageContext


def load_task(ctx: StageContext) -> TaskConfig:
    d = resolve_task_dir(ctx.project.task)
    return TaskConfig.model_validate(yaml.safe_load((d / "task.yaml").read_text(encoding="utf-8")))


def resolve_repo_path(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    for base in (Path.cwd(), repo_root()):
        if (base / p).exists():
            return (base / p).resolve()
    return (repo_root() / p).resolve()


def new_feedback(ctx: StageContext, s: dict, kind: str) -> Approval | None:
    """返回尚未处理过的最新一条 feedback（同一条只返回一次）。"""
    if not ctx.feedback:
        return None
    fb = ctx.feedback[-1]
    if fb.kind != kind or s.get("handled_feedback") == fb.approval_id:
        return None
    s["handled_feedback"] = fb.approval_id
    return fb


def feedback_note(fb: Approval) -> str:
    verb = {"changes_requested": "退回修改", "rejected": "拒绝"}.get(fb.status, fb.status)
    return f"按用户意见修改（{verb}）：{fb.decision_comment or '（未填写意见）'}"
