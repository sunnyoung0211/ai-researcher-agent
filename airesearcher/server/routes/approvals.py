"""审批（详细设计 1 第 9.2 节“审批”）。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends

from airesearcher.core.errors import NotFound
from airesearcher.core.models.approval import Approval
from airesearcher.core.models.common import VersionRef
from airesearcher.core.project import Project

from ..deps import manager
from ..manager import ProjectManager
from ..schemas import ApprovalDetail, DecisionRequest, ExtraRef

router = APIRouter(prefix="/api/projects/{pid}/approvals", tags=["approvals"])
MAX_INLINE = 40_000


def text_of(p: Project, ref: VersionRef | None) -> str | None:
    """文本型产物返回内容；目录型产物返回清单；二进制返回 None。"""
    if ref is None:
        return None
    try:
        data = p.archive.get(ref)
    except NotFound:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if len(text) <= MAX_INLINE else text[:MAX_INLINE] + "\n……（内容过长，已截断）"


@router.get("", response_model=list[Approval])
def list_approvals(pid: str, status: str | None = None, kind: str | None = None,
                   m: ProjectManager = Depends(manager)) -> list[Approval]:
    return m.get(pid).approvals.list(kind=kind, status=status)  # type: ignore[arg-type]


@router.get("/{aid}", response_model=ApprovalDetail)
def get_approval(pid: str, aid: str, m: ProjectManager = Depends(manager)) -> ApprovalDetail:
    p = m.get(pid)
    a = p.approvals.get(aid)
    extra = []
    for r in a.extra_refs:
        url = f"/api/projects/{pid}/artifacts/content?artifact_id={quote(r.artifact_id)}&version={r.version}"
        content = text_of(p, r)
        extra.append(ExtraRef(artifact_id=r.artifact_id, version=r.version, sha256=r.sha256,
                              title=r.artifact_id.rsplit("/", 1)[-1], url=url,
                              content=content if content and len(content) < 8000 else None))
    return ApprovalDetail(approval=a, target_content=text_of(p, a.target), previous_content=text_of(p, a.previous),
                          target_path=str(p.archive.path_of(a.target.artifact_id)), extra=extra)


@router.post("/{aid}/decision", response_model=Approval)
def decide(pid: str, aid: str, body: DecisionRequest, m: ProjectManager = Depends(manager)) -> Approval:
    a = m.get(pid).approvals.decide(aid, body.decision, body.expected_sha256, body.comment,
                                    request_id=body.request_id, by="user")
    m.wait_settled(pid, lambda e: e.ck.pending_approval != aid)
    return a
