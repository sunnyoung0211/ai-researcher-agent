"""检查点 .state/checkpoint.json（详细设计 1 第 5.5 节）。

先写临时文件再原子 rename；同时复制到 .state/checkpoints/ck-NNNNNN.json，保留最近 20 个。
状态发生变化的那一次另存到 .state/milestones/（不删除），回滚（5.7）通常回到这些“里程碑”。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from airesearcher.core.errors import NotFound
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


class CheckpointBrief(BaseModel):
    """检查点列表的一行（air checkpoints、GET /checkpoints）。"""

    checkpoint_id: str
    saved_at: datetime
    state: ProjectState
    resume_to: ProjectState | None = None
    reason: str = ""
    milestone: bool = False  # 状态变化时保存的，长期保留


def save(root: Path, ck: Checkpoint) -> Checkpoint:
    d = _dir(root)
    hist = d / "checkpoints"
    hist.mkdir(parents=True, exist_ok=True)
    prev = load(root)
    n = int(ck.checkpoint_id.split("-")[1]) + 1
    ck.checkpoint_id = f"ck-{n:06d}"
    ck.saved_at = now()
    atomic_write_json(d / "checkpoint.json", ck)
    atomic_write_json(hist / f"{ck.checkpoint_id}.json", ck)
    if prev is None or prev.state != ck.state:
        (d / "milestones").mkdir(exist_ok=True)
        atomic_write_json(d / "milestones" / f"{ck.checkpoint_id}.json", ck)
    old = sorted(hist.glob("ck-*.json"))
    for p in old[:-KEEP]:
        p.unlink(missing_ok=True)
    return ck


def _files(root: Path) -> dict[str, tuple[Path, bool]]:
    out = {p.stem: (p, False) for p in (_dir(root) / "checkpoints").glob("ck-*.json")}
    out.update({p.stem: (p, True) for p in (_dir(root) / "milestones").glob("ck-*.json")})
    return out


def history(root: Path) -> list[str]:
    return sorted(_files(root))


def load_history(root: Path, checkpoint_id: str) -> Checkpoint:
    f = _files(root).get(checkpoint_id)
    if f is None:
        raise NotFound(f"找不到检查点 {checkpoint_id}（用 air checkpoints 查看可回滚的检查点）")
    return Checkpoint.model_validate(read_json(f[0]))


def briefs(root: Path) -> list[CheckpointBrief]:
    """所有保留着的检查点，从新到旧。"""
    out = []
    for cid, (p, milestone) in sorted(_files(root).items(), reverse=True):
        try:
            ck = Checkpoint.model_validate(read_json(p))
        except ValueError:
            continue
        out.append(CheckpointBrief(checkpoint_id=cid, saved_at=ck.saved_at, state=ck.state, resume_to=ck.resume_to,
                                   reason=ck.reason, milestone=milestone))
    return out
