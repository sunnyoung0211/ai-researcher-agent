"""任务配置与实验计划（详细设计 3 第 3.2、4.2~4.5 节；实验同学起草）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .common import VersionRef
from .frontmatter import split_front_matter

ExperimentKind = Literal["baseline", "main", "ablation", "control", "replication", "exploration"]


class TaskMetric(BaseModel):
    name: str
    direction: Literal["higher", "lower"]
    required: bool = False


class TaskData(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    source: str
    license: str = ""
    prepare: list[str] = []
    files: list[dict] = []


class TaskConfig(BaseModel):
    """tasks/<name>/task.yaml：主流程只读这个文件来了解领域（D8）。"""

    model_config = ConfigDict(extra="allow")
    name: str
    domain: str
    description: str
    template_dir: str
    entrypoint: list[str]
    trial_args: list[str] = []
    config_schema: str | None = None
    metrics: list[TaskMetric]
    splits: dict[str, dict] = {}
    data: list[TaskData] = []
    models: list[str] = []
    methods_available: list[str] = []
    semantic_keys: list[str] = []
    repair_tunable: list[str] = []
    protected_files: list[str] = []
    sanity: dict[str, Any] = {}
    resources: dict[str, Any] = {}


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    kind: ExperimentKind
    purpose: str
    hypotheses: list[str] = []
    method: str
    variables: dict[str, list[Any]] = {}
    fixed: dict[str, Any] = {}
    seeds: list[int] = [0, 1, 2]
    eval_condition: Literal["explore", "final"] = "final"
    depends_on: list[str] = []
    target_env: str = "local"
    needs_code: bool = False
    est_minutes_per_run: float = 1
    success_criteria: str = ""
    failure_criteria: str = ""


class Comparison(BaseModel):
    id: str
    question: str
    experiments: list[str]
    group_by: list[str]
    metric: str
    filter: dict[str, Any] = {}


class ReplicationPolicy(BaseModel):
    max_extra_seeds_per_group: int = 3
    trigger: str = ""


class StoppingConditions(BaseModel):
    max_rounds: int = 3
    no_progress_rounds: int = 2
    budget_fraction: float = 0.9


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    plan_id: str
    idea_ref: VersionRef
    task_config: str
    experiments: list[ExperimentSpec]
    comparisons: list[Comparison]
    replication_policy: ReplicationPolicy = ReplicationPolicy()
    stopping_conditions: StoppingConditions = StoppingConditions()
    budget_estimate: dict[str, Any] = {}
    approval_points: list[str] = []
    reuse_runs: list[str] = []


class PlanTask(BaseModel):
    """plan/tasks.json 中的一项（4.3）。"""

    task_key: str  # "E2-train_size=500-seed=1"
    experiment_id: str
    kind: ExperimentKind
    config: dict[str, Any]
    eval_condition: Literal["explore", "final"]
    depends_on: list[str] = []
    expected_metrics: list[str] = []


class PrecheckItem(BaseModel):
    """plan/precheck.json 中的一项（4.5）。"""

    check: str
    status: Literal["pass", "warn", "fail"]
    detail: str = ""


def parse_plan_md(text: str) -> ExperimentPlan:
    """论文阶段和 GUI 用它读 experiment_plan.md。"""
    data, _ = split_front_matter(text)
    return ExperimentPlan.model_validate(data)
