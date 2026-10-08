"""【实验】计划阶段的假实现（PlanDrafting）。不调用模型。

读已批准的 selected.md，按详细设计 3 第 4 节写：
- plan/experiment_plan.md   ExperimentPlan（YAML 头部 + 正文）：基线 / 主方法 / 消融 × 3 个种子
- plan/tasks.json           PlanTask 列表（变量笛卡尔积 × 种子，确定性展开）
- plan/precheck.json        PrecheckItem 列表（简化的执行前检查）
然后提交 plan 审批。被退回、或 idea 有新的获批版本时，生成修订版。

演示用的开关（project.yaml 的 dev）：smoke_seconds 每个运行跑几秒；smoke_fail {task_key: fail_mode} 让某个运行失败。
"""

from __future__ import annotations

import itertools
import json

from airesearcher.core.fsutil import dumps
from airesearcher.core.models.frontmatter import render_front_matter
from airesearcher.core.models.idea import parse_selected_md
from airesearcher.core.models.plan import (
    Comparison,
    ExperimentPlan,
    ExperimentSpec,
    PlanTask,
    PrecheckItem,
    StoppingConditions,
    TaskConfig,
    parse_plan_md,
)
from airesearcher.engine.stage import Continue, NeedsApproval, StageContext, StepResult

from .common import feedback_note, load_task, new_feedback

PLAN = "plan/experiment_plan.md"
TASKS = "plan/tasks.json"
PRECHECK = "plan/precheck.json"


def build_plan(ctx: StageContext, task: TaskConfig) -> ExperimentPlan:
    idea_ref = ctx.approved["idea"]
    idea = parse_selected_md(ctx.archive.read_text(idea_ref))
    methods = (task.methods_available or ["baseline", "main", "ablation"]) + ["main", "ablation"]
    seconds = ctx.project.dev.smoke_seconds
    primary = idea.primary_metric().name
    h = [x.id for x in idea.hypotheses]
    fixed = {"seconds": seconds}
    exps = [
        ExperimentSpec(id="E1", kind="baseline", purpose=f"基线 {methods[0]}", hypotheses=h, method=methods[0],
                       fixed=fixed, est_minutes_per_run=round(seconds / 60, 3),
                       success_criteria="3 个种子均完成", failure_criteria="任一种子修复后仍失败"),
        ExperimentSpec(id="E2", kind="main", purpose=f"主方法 {methods[1]} 与基线比较", hypotheses=h,
                       method=methods[1], fixed=fixed, est_minutes_per_run=round(seconds / 60, 3)),
        ExperimentSpec(id="E3", kind="ablation", purpose=f"消融 {methods[2]}", hypotheses=[], method=methods[2],
                       fixed=fixed, est_minutes_per_run=round(seconds / 60, 3), depends_on=[]),
    ]
    runs = sum(len(e.seeds) for e in exps)
    return ExperimentPlan(
        plan_id="plan-001", idea_ref=idea_ref, task_config=ctx.project.task, experiments=exps,
        comparisons=[
            Comparison(id="C1", question=f"{methods[1]} 是否优于基线 {methods[0]}", experiments=["E1", "E2"],
                       group_by=["method"], metric=primary),
            Comparison(id="C2", question=f"消融：{methods[2]} 与 {methods[1]} 的差别", experiments=["E2", "E3"],
                       group_by=["method"], metric=primary),
        ],
        stopping_conditions=StoppingConditions(max_rounds=ctx.project.limits.max_rounds),
        budget_estimate={"runs": runs, "wall_hours": round(runs * seconds / 3600, 4), "gpu_hours": 0},
        approval_points=["每轮结束的重要过程日志", "计划外实验", "最终手稿"],
    )


def expand_tasks(plan: ExperimentPlan, task: TaskConfig, fail: dict[str, str]) -> list[PlanTask]:
    required = [m.name for m in task.metrics if m.required]
    out = []
    for e in plan.experiments:
        keys = sorted(e.variables)
        for combo in itertools.product(*(e.variables[k] for k in keys)):
            for seed in e.seeds:
                parts = [f"{k}={v}" for k, v in zip(keys, combo, strict=True)] + [f"seed={seed}"]
                key = "-".join([e.id, *parts])
                cfg = {"method": e.method, **e.fixed, **dict(zip(keys, combo, strict=True)), "seed": seed,
                       "fail_mode": fail.get(key, "none")}
                out.append(PlanTask(task_key=key, experiment_id=e.id, kind=e.kind, config=cfg,
                                    eval_condition=e.eval_condition, depends_on=e.depends_on,
                                    expected_metrics=required))
    return out


def precheck(plan: ExperimentPlan, task: TaskConfig, primary: str, wall_budget: float | None) -> list[PrecheckItem]:
    task_metrics = {m.name for m in task.metrics}
    covered = {h for e in plan.experiments for h in e.hypotheses}
    est_h = plan.budget_estimate.get("wall_hours", 0)
    items = [
        PrecheckItem(check="license", status="warn" if any(not d.license or "未核实" in d.license for d in task.data)
                     else "pass", detail="任务没有外部数据" if not task.data else "见 task.yaml"),
        PrecheckItem(check="leakage_split", status="pass", detail="所有实验都在 final 划分上评价，没有探索性实验"),
        PrecheckItem(check="metric", status="pass" if primary in task_metrics else "fail",
                     detail=f"主要指标 {primary}"),
        PrecheckItem(check="fairness", status="pass", detail="各实验的变量取值与种子数相同"),
        PrecheckItem(check="repeats", status="pass" if all(len(e.seeds) >= 3 for e in plan.experiments) else "warn",
                     detail="每个实验 3 个种子"),
        PrecheckItem(check="coverage", status="pass" if covered else "fail", detail=f"覆盖假设 {sorted(covered)}"),
        PrecheckItem(check="budget", status="pass" if not wall_budget or est_h <= 0.6 * wall_budget else "warn",
                     detail=f"估计 {est_h} 小时，预算 {wall_budget} 小时"),
    ]
    return items


def render_plan(plan: ExperimentPlan, tasks: list[PlanTask], checks: list[PrecheckItem], notes: list[str]) -> str:
    lines = ["# 目的与假设", f"依据 idea：`{plan.idea_ref.artifact_id}` v{plan.idea_ref.version}。", "",
             "# 实验矩阵", "| ID | 类型 | 方法 | 变量 | 种子 | 评价条件 |", "|---|---|---|---|---|---|"]
    for e in plan.experiments:
        lines.append(f"| {e.id} | {e.kind} | {e.method} | {e.variables or '—'} | {e.seeds} | {e.eval_condition} |")
    lines += ["", "# 变量与控制", f"固定参数：{plan.experiments[0].fixed}；共 {len(tasks)} 次运行。", "",
              "# 比较与统计方法"]
    lines += [f"- {c.id}：{c.question}（{c.experiments}，按 {c.group_by} 分组，指标 {c.metric}）"
              for c in plan.comparisons]
    lines += ["- 每组报告均值、样本标准差、t 分布 95% 区间；组间用 Welch t 检验（仅供参考）。", "",
              "# 资源估计", f"{plan.budget_estimate}", "",
              "# 停止条件", f"{plan.stopping_conditions.model_dump()}", "",
              "# 执行前检查结果"]
    lines += [f"- {c.check}：{c.status}（{c.detail}）" for c in checks]
    lines += ["", "# 审批点", *[f"- {a}" for a in plan.approval_points]]
    if notes:
        lines += ["", "# 修订说明", *[f"- {n}" for n in notes]]
    return render_front_matter(plan.model_dump(mode="json"), "\n".join(lines) + "\n")


class FakePlanStage:
    name = "plan"

    def step(self, ctx: StageContext) -> StepResult:
        s = ctx.scratch.setdefault("plan", {})
        fb = new_feedback(ctx, s, "plan")
        if fb is not None:
            s.setdefault("notes", []).append(feedback_note(fb))
            s["regen"] = True
        idea_ref = ctx.approved["idea"]
        latest = ctx.archive.latest(PLAN)
        if latest is None or s.get("idea_sha") != idea_ref.sha256 or s.get("regen"):
            ctx.progress("计划（假实现）：生成实验计划", 1, 2, "steps")
            if latest is not None and s.get("idea_sha") != idea_ref.sha256:
                s.setdefault("notes", []).append(f"idea 有新的获批版本 v{idea_ref.version}，更新 idea_ref")
            task = load_task(ctx)
            plan = build_plan(ctx, task)
            tasks = expand_tasks(plan, task, ctx.project.dev.smoke_fail)
            primary = plan.comparisons[0].metric
            wall = ctx.project.budget.get("wall_hours")
            checks = precheck(plan, task, primary, wall.hard if wall else None)
            notes = s.get("notes", [])
            md = render_plan(plan, tasks, checks, notes)
            parse_plan_md(md)  # 写入前校验
            tasks_ref = ctx.archive.put(TASKS, "plan", dumps([t.model_dump(mode="json") for t in tasks]),
                                        parents=[idea_ref], producer="agent:plan/expand")
            pre_ref = ctx.archive.put(PRECHECK, "precheck", dumps([c.model_dump() for c in checks]),
                                      parents=[idea_ref], producer="agent:plan/precheck")
            ctx.archive.put(PLAN, "plan", md, parents=[r for r in (latest, idea_ref, tasks_ref, pre_ref) if r],
                            producer="agent:plan/writer", note=notes[-1] if notes else "初版")
            s["idea_sha"] = idea_ref.sha256
            s.pop("regen", None)
            return Continue("实验计划已保存")

        ctx.progress("计划（假实现）：等待审批", 2, 2, "steps")
        plan = parse_plan_md((ctx.root / PLAN).read_text(encoding="utf-8"))
        n_runs = plan.budget_estimate.get("runs")
        checks = [PrecheckItem.model_validate(c) for c in json.loads((ctx.root / PRECHECK).read_text())]
        warns = [c.check for c in checks if c.status != "pass"]
        extra = [r for r in (ctx.archive.latest(PRECHECK), ctx.archive.latest(TASKS)) if r]
        return NeedsApproval(
            target=latest, kind="plan",
            summary=f"（假实现）{len(plan.experiments)} 个实验（基线/主方法/消融），{n_runs} 次运行；"
                    f"执行前检查 {len(warns)} 项 warn/fail" + (f"：{', '.join(warns)}" if warns else ""),
            impact="首次提交" if latest.version == 1 else "修订版：旧计划下完成的运行不会被当作新计划的结果",
            extra_refs=extra,
        )


def create_stage() -> FakePlanStage:
    return FakePlanStage()
