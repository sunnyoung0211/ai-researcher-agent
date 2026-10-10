"""模型配置（详细设计 1 第 6.1 节）：查看已登记的模型、Key 是否已设置、每个阶段实际用哪个模型；试调用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from airesearcher.llm.gateway import ping_model
from airesearcher.llm.models import ModelRegistry
from airesearcher.stages import STAGE_NAMES

from ..deps import manager
from ..manager import ProjectManager
from ..schemas import RequestIdOnly

router = APIRouter(prefix="/api/models", tags=["models"])


def _project_models(m: ProjectManager, project_id: str | None) -> dict | None:
    return m.get(project_id).config.models if project_id else None


@router.get("")
def list_models(project_id: str | None = None, m: ProjectManager = Depends(manager)) -> dict:
    """不返回任何 Key，只返回“是否已设置”。"""
    return ModelRegistry(_project_models(m, project_id)).summary(list(STAGE_NAMES))


@router.post("/{alias}/test")
def test_model(alias: str, body: RequestIdOnly, project_id: str | None = None,
               m: ProjectManager = Depends(manager)) -> dict:
    return ping_model(alias, _project_models(m, project_id))
