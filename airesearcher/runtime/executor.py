"""执行器接口（详细设计 3 第 6.1 节；实验同学负责）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from airesearcher.core.models.run import MetricRecord, Resources, RunRecord, RunStatus


class RunSpec(BaseModel):
    record: RunRecord
    run_dir: Path
    config: dict
    trial: bool = False
    gpu: bool = False


class JobHandle(BaseModel):
    run_id: str
    wrapper_pid: int | None = None


class LogChunk(BaseModel):
    text: str
    next_offset: int
    eof: bool


class RunOutputs(BaseModel):
    run_id: str
    status: RunStatus
    metrics: list[MetricRecord]
    resources: Resources | None = None
    files: list[str] = []


@runtime_checkable
class Executor(Protocol):
    def probe(self) -> dict[str, Any]: ...
    def prepare(self, spec: RunSpec) -> None: ...
    def submit(self, spec: RunSpec) -> JobHandle: ...
    def status(self, run_id: str) -> RunStatus: ...
    def logs(self, run_id: str, stream: str, offset: int) -> LogChunk: ...
    def cancel(self, run_id: str) -> None: ...
    def collect(self, run_id: str) -> RunOutputs: ...
    def reconcile(self, run_id: str) -> RunStatus: ...
    def snapshot_env(self, run_id: str) -> dict: ...
