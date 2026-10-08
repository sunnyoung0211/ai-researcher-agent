"""检查点 .state/checkpoint.json（详细设计 1 第 5.5 节）。

先写临时文件再原子 rename；同时复制到 .state/checkpoints/ck-NNNNNN.json，保留最近 20 个。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from airesearcher.core.fsutil import atomic_write_json, now, read_json
from airesearcher.core.models.common import ProjectState, VersionRef

KEEP = 20


class Checkpoint(BaseModel):
    checkpoint_id: str = "ck-000000"
    saved_at: datetime = Field(default_factory=now)
    state: ProjectState = ProjectState.IdeaDrafting
    resume_to: ProjectState | None = None
    pending_approval: str | None = None
    pending_question: str | None = None
    approved: dict[str, VersionRef] = {}
    stale: list[str] = []
    feedback_approval_ids: list[str] = []
    entry: dict | None = None  # Advance.to_json()
    scratch: dict[str, Any] = {}
    consecutive_errors: int = 0
    last_event_seq: int = 0
    # 主干增补字段（文档 5.5 的示例之外）：
    answer: dict | None = None  # 待交给阶段的一次性回答（Answer），阶段下一次 step 后清空
    reason: str = ""  # 进入 Paused / Failed / BudgetExhausted 的原因，状态页显示
    rejected_approval: str | None = None  # 被拒绝的审批，等待用户回答 redo/end


def _dir(root: Path) -> Path:
    return Path(root) / ".state"


def load(root: Path) -> Checkpoint | None:
    p = _dir(root) / "checkpoint.json"
    if not p.exists():
        return None
    return Checkpoint.model_validate(read_json(p))


def save(root: Path, ck: Checkpoint) -> Checkpoint:
    d = _dir(root)
    hist = d / "checkpoints"
    hist.mkdir(parents=True, exist_ok=True)
    n = int(ck.checkpoint_id.split("-")[1]) + 1
    ck.checkpoint_id = f"ck-{n:06d}"
    ck.saved_at = now()
    atomic_write_json(d / "checkpoint.json", ck)
    atomic_write_json(hist / f"{ck.checkpoint_id}.json", ck)
    old = sorted(hist.glob("ck-*.json"))
    for p in old[:-KEEP]:
        p.unlink(missing_ok=True)
    return ck


def history(root: Path) -> list[str]:
    return sorted(p.stem for p in (_dir(root) / "checkpoints").glob("ck-*.json"))


def load_history(root: Path, checkpoint_id: str) -> Checkpoint:
    return Checkpoint.model_validate(read_json(_dir(root) / "checkpoints" / f"{checkpoint_id}.json"))
