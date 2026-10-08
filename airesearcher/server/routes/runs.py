"""运行（数据由【实验】的文件提供，接口由主干实现；详细设计 1 第 9.2 节“运行”）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from airesearcher.core.models.run import RunStatus, RunSummary, RunView
from airesearcher.runtime.executor import LogChunk
from airesearcher.services import runs as run_service

from ..deps import manager
from ..manager import ProjectManager
from ..schemas import RequestIdOnly

router = APIRouter(prefix="/api/projects/{pid}/runs", tags=["runs"])


@router.get("", response_model=list[RunSummary])
def list_runs(pid: str, experiment_id: str | None = None, status: str | None = None,
              m: ProjectManager = Depends(manager)) -> list[RunSummary]:
    return [r.summary() for r in run_service.list_runs(m.get(pid).root, experiment_id=experiment_id, state=status)]


@router.get("/{run_id}", response_model=RunView)
def get_run(pid: str, run_id: str, m: ProjectManager = Depends(manager)) -> RunView:
    return run_service.load_run(m.get(pid).root, run_id)


@router.get("/{run_id}/logs", response_model=LogChunk)
def get_logs(pid: str, run_id: str, stream: str = "stdout", offset: int = 0,
             m: ProjectManager = Depends(manager)) -> LogChunk:
    return m.engine(pid).executor.logs(run_id, stream, offset)


@router.post("/{run_id}/cancel", response_model=RunStatus)
def cancel_run(pid: str, run_id: str, body: RequestIdOnly, m: ProjectManager = Depends(manager)) -> RunStatus:
    ex = m.engine(pid).executor
    ex.cancel(run_id)
    m.get(pid).events.append("run.cancel_requested", f"用户请求取消运行 {run_id}", actor="user", run_id=run_id)
    return ex.status(run_id)
