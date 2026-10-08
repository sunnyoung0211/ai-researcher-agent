"""产物版本（详细设计 1 第 3.3 节）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .common import ArtifactKind, Producer, VersionRef


class ArtifactVersion(BaseModel):
    ref: VersionRef
    kind: ArtifactKind
    path: str  # 当前位置（= artifact_id）
    stored_at: str | None  # .archive 内的拷贝路径；只追加的目录型产物为 None
    is_dir: bool = False
    exclude: list[str] = []
    parents: list[VersionRef] = []
    producer: Producer
    note: str = ""
    created_at: datetime


class LineageNode(BaseModel):
    ref: VersionRef
    kind: str | None = None
    producer: str | None = None
    note: str = ""
    parents: list[LineageNode] = []
