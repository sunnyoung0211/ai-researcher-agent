"""导出 zip（详细设计 1 第 9.2 节；GUI P10“导出”按钮）。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from airesearcher.services import export as export_service

from ..deps import manager
from ..manager import ProjectManager

router = APIRouter(prefix="/api/projects/{pid}", tags=["export"])


@router.get("/export", response_class=FileResponse)
def export(pid: str, what: Literal["paper", "archive"] = "paper", m: ProjectManager = Depends(manager)) -> FileResponse:
    """paper：LaTeX 源文件、PDF、图表与脚本、claims.jsonl、核验报告、ARCHIVE_INDEX.md；archive：整个研究档案。"""
    p = m.get(pid)
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix=f"air-export-{pid}-")
    os.close(fd)
    n = export_service.write_zip(p.root, what, Path(tmp), p.project_id, p.config.title)
    p.events.append("project.exported", f"导出{'论文' if what == 'paper' else '研究档案'}（{n} 个文件）",
                    actor="user", data={"what": what, "files": n})
    return FileResponse(tmp, media_type="application/zip", filename=f"{pid}-{what}.zip",
                        background=BackgroundTask(lambda: Path(tmp).unlink(missing_ok=True)))
