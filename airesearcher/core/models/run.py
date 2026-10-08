"""运行目录协议（详细设计 3 第 5 节；实验同学起草）。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel

from .common import VersionRef

RunKind = Literal["baseline", "main", "ablation", "control", "replication", "exploration", "trial", "smoke"]
FailureReason = Literal["nonzero_exit", "timeout", "cancelled", "lost", "oom", "missing_metrics", "wrapper_error"]


class InputRef(BaseModel):
    name: str
    path: str
    sha256: str


class RunRecord(BaseModel):  # run.json
    run_id: str
    task_key: str
    experiment_id: str
    kind: RunKind
    plan_ref: VersionRef
    idea_ref: VersionRef
    code_commit: str
    config_sha256: str
    inputs: list[InputRef] = []
    eval_condition: Literal["explore", "final"]
    seed: int | None = None
    executor: str = "local"
    command: list[str]
    timeout_s: int
    retry_of: str | None = None
    repair_attempt: int = 0
    # 主干增补（契约变更，见详细设计 3 第 5.2 节）：包装器据此判断“必需指标是否已上报”（5.4）
    required_metrics: list[str] = []
    created_at: datetime


class RunState(str, Enum):
    created = "created"
    preparing = "preparing"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    unknown = "unknown"


TERMINAL_RUN_STATES = {RunState.succeeded, RunState.failed, RunState.cancelled}
ACTIVE_RUN_STATES = {RunState.created, RunState.preparing, RunState.queued, RunState.running, RunState.unknown}


class RunStatus(BaseModel):  # status.json
    run_id: str
    state: RunState
    host: str
    pid: int | None = None
    pgid: int | None = None
    wrapper_pid: int | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    ended_at: datetime | None = None
    exit_code: int | None = None
    failure_reason: FailureReason | None = None
    error_class: str | None = None


class MetricRecord(BaseModel):  # metrics.jsonl 的一行
    seq: int
    ts: datetime
    name: str
    value: float
    split: str | None = None
    step: int | None = None


class Resources(BaseModel):  # resources.json
    wall_seconds: float
    cpu_seconds: float = 0.0
    gpu_seconds: float = 0.0
    max_rss_mb: float | None = None


class RunSummary(BaseModel):
    """API /runs 列表的一行（GUI 运行页）。"""

    run_id: str
    task_key: str
    experiment_id: str
    kind: str
    state: RunState
    failure_reason: str | None = None
    retry_of: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class RunView(BaseModel):
    """services.runs.load_run() 的返回：run.json + status.json + 指标 + 资源 + 环境摘要。"""

    record: RunRecord
    status: RunStatus
    metrics: list[MetricRecord] = []
    resources: Resources | None = None
    environment: dict[str, Any] | None = None

    def summary(self) -> RunSummary:
        return RunSummary(
            run_id=self.record.run_id, task_key=self.record.task_key,
            experiment_id=self.record.experiment_id, kind=self.record.kind, state=self.status.state,
            failure_reason=self.status.failure_reason, retry_of=self.record.retry_of,
            created_at=self.record.created_at, started_at=self.status.started_at,
            ended_at=self.status.ended_at,
        )
