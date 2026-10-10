"""模板上传与列表（详细设计 1 第 9.2 节；GUI P2、P14）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile

from airesearcher.core import templates
from airesearcher.core.models.template import TemplateInfo, TemplateList

from ..deps import manager
from ..manager import ProjectManager

router = APIRouter(prefix="/api/projects/{pid}/templates", tags=["templates"])


@router.get("", response_model=TemplateList)
def list_templates(pid: str, m: ProjectManager = Depends(manager)) -> TemplateList:
    """当前使用的模板、每个上传版本及其检查结果（检查由论文阶段写入 check.json）。"""
    return templates.list_templates(m.get(pid))


@router.post("", response_model=TemplateInfo)
async def upload_template(pid: str, file: UploadFile = File(..., description="模板 zip"),
                          template_id: str | None = Form(None, description="作为已有模板的新版本，如 tpl-01"),
                          request_id: str = Form(""), m: ProjectManager = Depends(manager)) -> TemplateInfo:
    """上传模板 zip（multipart）。解压到 templates/<tpl_id>/v<N>/original/ 并让项目改用它。"""
    data = await file.read(templates.MAX_ZIP_BYTES + 1)
    return m.upload_template(pid, data, file.filename or "template.zip", template_id or None, request_id)
