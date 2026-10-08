"""审批服务（详细设计 1 第 3.6、4.2 节）。

审批文件 approvals/<approval_id>.json 在状态变化时整体原子重写；每次变化同时写事件。
用户的决定写进文件后，通过 on_change 回调唤醒该项目的引擎线程；引擎以文件为准做状态转换，
所以“写文件后、检查点前崩溃”时，重启后能按文件补做转换（5.6 第 4 步）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .archive import Archive
from .errors import NotFound, StaleApproval, ValidationFailed
from .events import Events
from .fsutil import atomic_write_json, now, read_json
from .models.approval import Approval, Decision
from .models.common import ApprovalKind, VersionRef

DECISIONS = ("approved", "changes_requested", "rejected")


class Approvals:
    def __init__(self, root: Path, events: Events, archive: Archive):
        self.root = Path(root)
        self.dir = self.root / "approvals"
        self.events = events
        self.archive = archive
        self._lock = threading.RLock()
        self.on_change: list[Callable[[Approval], None]] = []

    # ---------- 读 ----------
    def _path(self, approval_id: str) -> Path:
        return self.dir / f"{approval_id}.json"

    def get(self, approval_id: str) -> Approval:
        p = self._path(approval_id)
        if not p.exists():
            raise NotFound(f"找不到审批 {approval_id}")
        return Approval.model_validate(read_json(p))

    def list(self, kind: ApprovalKind | None = None, status: str | None = None) -> list[Approval]:
        """按创建时间排序。"""
        if not self.dir.exists():
            return []
        out = [Approval.model_validate(read_json(p)) for p in sorted(self.dir.glob("ap-*.json"))]
        out = [a for a in out if (kind is None or a.kind == kind) and (status is None or a.status == status)]
        return sorted(out, key=lambda a: (a.created_at, a.approval_id))

    def pending(self) -> list[Approval]:
        return self.list(status="pending")

    def latest_approved(self, kind: ApprovalKind) -> Approval | None:
        done = self.list(kind=kind, status="approved")
        return done[-1] if done else None

    # ---------- 写 ----------
    def _save(self, a: Approval) -> None:
        atomic_write_json(self._path(a.approval_id), a)

    def _next_id(self) -> str:
        nums = [int(p.stem.split("-")[1]) for p in self.dir.glob("ap-*.json")] if self.dir.exists() else []
        return f"ap-{max(nums, default=0) + 1:04d}"

    def _notify(self, a: Approval) -> None:
        for fn in list(self.on_change):
            try:
                fn(a)
            except Exception:
                pass

    def request(
        self,
        target: VersionRef,
        kind: ApprovalKind,
        summary: str,
        impact: str,
        extra_refs: list[VersionRef] | None = None,
    ) -> str:
        with self._lock:
            # 同一产物已有 pending 审批的，旧的自动标为 superseded
            for old in self.list(status="pending"):
                if old.target.artifact_id == target.artifact_id:
                    self.supersede(old.approval_id, "同一产物提交了新的审批请求")
            prev = self.latest_approved(kind)
            a = Approval(
                approval_id=self._next_id(), kind=kind, target=target,
                previous=prev.target if prev else None, summary=summary, impact=impact,
                extra_refs=list(extra_refs or []), status="pending", created_at=now(),
            )
            self._save(a)
        self.events.append(
            "approval.requested", f"请求审批 {kind}：{target.artifact_id} v{target.version}", actor="engine",
            refs=[target], data={"approval_id": a.approval_id, "kind": kind},
        )
        self._notify(a)
        return a.approval_id

    def supersede(self, approval_id: str, reason: str) -> Approval:
        with self._lock:
            a = self.get(approval_id)
            if a.status != "pending":
                return a
            a.status = "superseded"
            a.decided_at = now()
            a.decision_comment = reason
            a.decided_by = "system"
            self._save(a)
        self.events.append(
            "approval.superseded", f"审批 {approval_id} 已被取代：{reason}", actor="system", refs=[a.target],
            data={"approval_id": approval_id},
        )
        self._notify(a)
        return a

    def decide(
        self,
        approval_id: str,
        decision: Decision,
        expected_sha256: str,
        comment: str = "",
        request_id: str = "",
        by: str = "user",
    ) -> Approval:
        if decision not in DECISIONS:
            raise ValidationFailed(f"decision 必须是 {DECISIONS} 之一")
        with self._lock:
            a = self.get(approval_id)
            # 1. 重复请求：原样返回上次结果，不再通知引擎
            if request_id and request_id in a.request_ids:
                return a
            # 2. 已经处理过
            if a.status != "pending":
                raise StaleApproval(
                    f"审批 {approval_id} 已是 {a.status} 状态，不能再次决定", {"status": a.status}
                )
            # 3. 版本过期
            latest = self.archive.latest(a.target.artifact_id)
            if expected_sha256 != a.target.sha256 or latest is None or latest.sha256 != a.target.sha256:
                self.supersede(approval_id, "提交的决定针对的版本已不是最新版本")
                raise StaleApproval(
                    f"审批 {approval_id} 针对的版本已过期", {"expected_sha256": expected_sha256,
                                                          "target_sha256": a.target.sha256}
                )
            # 4. 手稿有 blocker 时批准必须写意见
            if a.kind == "manuscript" and decision == "approved" and not comment.strip():
                blockers = latest_review_blockers(self.root)
                if blockers:
                    raise ValidationFailed(
                        f"核验报告中仍有 {blockers} 个阻断问题（blocker），批准时必须填写意见说明理由",
                        {"blockers": blockers},
                    )
            # 5. 写入决定、事件，然后通知引擎
            a.status = decision
            a.decided_at = now()
            a.decision_comment = comment
            a.decided_by = by
            if request_id:
                a.request_ids.append(request_id)
            self._save(a)
        label = {"approved": "批准", "changes_requested": "退回修改", "rejected": "拒绝"}[decision]
        self.events.append(
            "approval.decided", f"{label}审批 {approval_id}（{a.kind}）" + (f"：{comment}" if comment else ""),
            actor="user" if by == "user" else by, refs=[a.target],
            data={"approval_id": approval_id, "decision": decision},
        )
        self._notify(a)
        return a


def latest_review_blockers(root: Path) -> int:
    """最新核验报告 paper/review/review_v<N>.json 中的 blocker 数（没有报告时为 0）。"""
    review_dir = Path(root) / "paper" / "review"
    reports = []
    for p in review_dir.glob("review_v*.json"):
        try:
            reports.append((int(p.stem.removeprefix("review_v")), p))
        except ValueError:
            continue
    if not reports:
        return 0
    data = read_json(max(reports)[1])
    return int((data.get("counts") or {}).get("blocker", 0))
