"""事件日志 research_log.jsonl（详细设计 1 第 3.5 节）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .common import VersionRef


class Event(BaseModel):
    seq: int  # 项目内单调递增
    ts: datetime
    type: str
    actor: str  # "engine" | "agent:<stage>/<role>" | "user" | "system"
    refs: list[VersionRef] = []
    run_id: str | None = None
    summary: str  # 一句话，GUI 直接展示
    data: dict = {}
