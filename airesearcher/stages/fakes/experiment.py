"""【实验】执行与分析阶段的假实现（Executing / Analyzing）。不调用模型，但**真的运行**冒烟任务。

Executing（详细设计 3 第 10 节，简化）：
  setup   计划版本变化时：模板复制到 src/、写 configs/<exp>/<task_key>.yaml、git 提交
  monitor 查询运行状态，收集已结束的；失败的 → Stop(question)：skip / retry / end
  submit  按 permissions.exec.max_parallel_cpu 提交运行（LocalExecutor：包装器是脱离后台的独立进程）
  全部结束 → Advance(Analyzing)
Analyzing：
  aggregate 每个 comparison 一个汇总 + all（artifacts/aggregates/<id>.json/.csv/.md）
  log       写 logs/round_1.md，提交 log 审批
  审批通过 → Advance(Writing)；退回 → 修改日志再提交
background()：项目在等待状态时只收集已结束的运行、写 run.status_changed 事件，不提交新运行。

没有实现：试运行（trial）、编码 Agent、修复重试、多轮分析决策——这些是【实验】同学的真实实现要做的。
"""

from __future__ import annotations

import json
import shutil

from airesearcher.core.fsutil import now, rand_hex
from airesearcher.core.models.common import ProjectState, VersionRef
from airesearcher.core.models.plan import Comparison, PlanTask, parse_plan_md
from airesearcher.core.models.question import Question, QuestionOption
from airesearcher.core.models.run import TERMINAL_RUN_STATES, RunRecord, RunState
from airesearcher.engine.stage import Advance, Continue, NeedsApproval, StageContext, StepResult, Stop, Wait
from airesearcher.runtime.executor import RunSpec
from airesearcher.runtime.local import config_sha256, config_text, expand_entrypoint
from airesearcher.services import runs as run_service

from .common import feedback_note, load_task, resolve_repo_path

PLAN = "plan/experiment_plan.md"
TASKS = "plan/tasks.json"
DONE = ("done", "skipped")


def _collect_once(ctx: StageContext, run_id: str) -> bool:
    """收集一个已结束的运行；第一次收集时写 run.status_changed 事件。返回是否是第一次。"""
    first = ctx.archive.latest(f"runs/{run_id}") is None
    out = ctx.executor.collect(run_id)
    if first:
        st = out.status
        ctx.events.append("run.status_changed", f"运行 {run_id} 结束：{st.state.value}"
                          + (f"（{st.failure_reason}）" if st.failure_reason else ""),
                          actor="agent:experiment/executor", run_id=run_id,
                          data={"state": st.state.value, "failure_reason": st.failure_reason})
    return first


class FakeExperimentStage:
    name = "experiment"

    # ------------------------------------------------------------------ 入口
    def step(self, ctx: StageContext) -> StepResult:
        s = ctx.scratch.setdefault("experiment", {})
        if ctx.state == ProjectState.Analyzing:
            return self._analyze(ctx, s)
        return self._execute(ctx, s)

    def background(self, ctx: StageContext) -> None:
        s = ctx.scratch.get("experiment", {})
        for t in s.get("tasks", {}).values():
            if t["status"] == "running" and t["runs"]:
                rid = t["runs"][-1]
                if ctx.executor.status(rid).state in TERMINAL_RUN_STATES:
                    _collect_once(ctx, rid)

    # ------------------------------------------------------------------ Executing
    def _setup(self, ctx: StageContext, s: dict) -> None:
        plan_ref = ctx.approved["plan"]
        task = load_task(ctx)
        src = ctx.root / "src"
        if not any(src.glob("*.py")):  # 首次：把任务模板复制到 src/
            shutil.copytree(resolve_repo_path(task.template_dir), src, dirs_exist_ok=True)
        tasks = [PlanTask.model_validate(t) for t in json.loads((ctx.root / TASKS).read_text(encoding="utf-8"))]
        for t in tasks:
            p = ctx.root / "configs" / t.experiment_id / f"{t.task_key}.yaml"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(config_text(t.config), encoding="utf-8")
        commit = ctx.workspace.commit(f"实验：计划 v{plan_ref.version} 的代码与配置", author="agent:experiment")
        s.clear()
        s.update({
            "plan_sha": plan_ref.sha256, "plan_version": plan_ref.version, "commit": commit, "round": 1,
            "tasks": {t.task_key: {"status": "todo", "runs": [], "experiment_id": t.experiment_id,
                                   "kind": t.kind, "config": t.config, "eval_condition": t.eval_condition}
                      for t in tasks},
        })
        ctx.events.append("stage.decision", f"（假实现）准备执行计划 v{plan_ref.version}：{len(tasks)} 个运行",
                          actor="agent:experiment")

    def _adopt_orphans(self, ctx: StageContext, s: dict) -> None:
        """崩溃恢复：runs/ 里属于当前计划、但 scratch 里没记下的运行（提交后、检查点前崩溃），接管而不是重复提交。"""
        known = {rid for t in s["tasks"].values() for rid in t["runs"]}
        for rv in run_service.list_runs(ctx.root):
            rec = rv.record
            if rec.run_id in known or rec.plan_ref.sha256 != s["plan_sha"] or rec.task_key not in s["tasks"]:
                continue
            t = s["tasks"][rec.task_key]
            if t["status"] == "todo":
                t["runs"].append(rec.run_id)
                t["status"] = "running"

    def _execute(self, ctx: StageContext, s: dict) -> StepResult:
        if s.get("plan_sha") != ctx.approved["plan"].sha256:
            self._setup(ctx, s)
            return Continue("执行准备完成")
        tasks: dict = s["tasks"]
        self._adopt_orphans(ctx, s)

        # 回答：失败运行怎么处理
        if ctx.answer and ctx.answer.question_id == s.get("asked") and s.get("failed_task"):
            key = s.pop("failed_task")
            s.pop("asked", None)
            if ctx.answer.choice == "retry":
                tasks[key]["status"] = "todo"
                tasks[key]["config"]["fail_mode"] = "none"  # 假实现：模拟“用户已修好代码”
                tasks[key]["retry_of"] = tasks[key]["runs"][-1]
            else:
                tasks[key]["status"] = "skipped"
            ctx.events.append("stage.decision", f"失败运行 {key} 的处理：{ctx.answer.choice}",
                              actor="agent:experiment")

        # monitor：收集已结束的运行
        for key, t in tasks.items():
            if t["status"] != "running":
                continue
            rid = t["runs"][-1]
            st = ctx.executor.status(rid)
            if st.state == RunState.queued and not (ctx.executor.run_dir(rid) / "wrapper.log").exists():
                ctx.executor.submit(self._spec_of(ctx, rid))  # prepare 后、submit 前崩溃：安全地重新提交同一个 run_id
                continue
            if st.state not in TERMINAL_RUN_STATES:
                continue
            _collect_once(ctx, rid)
            if st.state == RunState.succeeded:
                t["status"] = "done"
            else:
                t["status"] = "failed"
                s["failed_task"] = key
                stderr = (ctx.executor.run_dir(rid) / "stderr.log")
                tail = stderr.read_text(errors="replace", encoding="utf-8")[-300:].strip() if stderr.exists() else ""
                run_ref = ctx.archive.latest(f"runs/{rid}")
                return Stop(
                    f"运行 {key} 失败（{st.failure_reason}）",
                    question=Question(
                        text=f"运行 **{key}**（{rid}）失败：{st.failure_reason}。\n\nstderr 末尾：\n```\n{tail}\n```\n"
                             "请选择处理方式：",
                        options=[
                            QuestionOption(id="skip", label="跳过这个组合，继续其他运行", default=True),
                            QuestionOption(id="retry", label="我已手动修改代码，重试（假实现会去掉故障设置）"),
                            QuestionOption(id="end", label="结束项目"),
                        ],
                        refs=[run_ref] if run_ref else [],
                    ),
                )

        # submit：按并发上限提交
        running = sum(1 for t in tasks.values() if t["status"] == "running")
        limit = max(1, ctx.project.permissions.exec.max_parallel_cpu)
        if not ctx.budget.check().exhausted:
            for key, t in tasks.items():
                if running >= limit:
                    break
                if t["status"] != "todo":
                    continue
                rid = self._submit(ctx, s, key, t)
                t["runs"].append(rid)
                t["status"] = "running"
                running += 1

        done = sum(1 for t in tasks.values() if t["status"] in DONE)
        ctx.progress(f"实验（假实现）：第 {s['round']} 轮执行中（{done}/{len(tasks)}）", done, len(tasks), "runs")
        if done == len(tasks):
            return Advance(ProjectState.Analyzing, f"第 {s['round']} 轮运行全部结束")
        return Wait("等待运行结束", seconds=ctx.project.dev.poll_seconds)

    def _submit(self, ctx: StageContext, s: dict, key: str, t: dict) -> str:
        task = load_task(ctx)
        rid = f"r-{now():%Y%m%d-%H%M%S}-{rand_hex(4)}"
        d = ctx.executor.run_dir(rid)
        rec = RunRecord(
            run_id=rid, task_key=key, experiment_id=t["experiment_id"], kind=t["kind"],
            plan_ref=ctx.approved["plan"], idea_ref=ctx.approved["idea"], code_commit=s["commit"],
            config_sha256=config_sha256(t["config"]), eval_condition=t["eval_condition"],
            seed=t["config"].get("seed"), command=expand_entrypoint(task, d, ctx.executor.python),
            timeout_s=int(ctx.project.experiment.get("run_timeout_s", 600)), retry_of=t.get("retry_of"),
            required_metrics=[m.name for m in task.metrics if m.required], created_at=now(),
        )
        spec = RunSpec(record=rec, run_dir=d, config=t["config"])
        ctx.executor.prepare(spec)
        ctx.executor.submit(spec)
        ctx.events.append("run.created", f"提交运行 {key}：{rid}", actor="agent:experiment", run_id=rid,
                          data={"task_key": key, "experiment_id": t["experiment_id"]})
        return rid

    def _spec_of(self, ctx: StageContext, rid: str) -> RunSpec:
        rv = run_service.load_run(ctx.root, rid, with_metrics=False)
        import yaml

        cfg = yaml.safe_load((ctx.executor.run_dir(rid) / "config.yaml").read_text(encoding="utf-8"))
        return RunSpec(record=rv.record, run_dir=ctx.executor.run_dir(rid), config=cfg)

    # ------------------------------------------------------------------ Analyzing
    def _analyze(self, ctx: StageContext, s: dict) -> StepResult:
        a = s.setdefault("analysis", {})
        fb = ctx.feedback[-1] if ctx.feedback else None
        if fb is not None and fb.kind == "log" and a.get("handled_feedback") != fb.approval_id:
            a["handled_feedback"] = fb.approval_id
            if fb.status == "approved":
                return Advance(ProjectState.Writing, "过程日志已批准；计划内实验全部完成，达到停止条件",
                               refs=[fb.target])
            a.setdefault("notes", []).append(feedback_note(fb))
            a.pop("log_done", None)

        plan_ref: VersionRef = ctx.approved["plan"]
        if a.get("aggregated_for") != plan_ref.sha256:
            ctx.progress("实验（假实现）：汇总结果", 1, 3, "steps")
            plan = parse_plan_md(ctx.archive.read_text(plan_ref))
            comps = list(plan.comparisons) + [Comparison(
                id="all", question="全部实验", experiments=[e.id for e in plan.experiments],
                group_by=["experiment_id", "method"], metric=plan.comparisons[0].metric)]
            for c in comps:
                agg = run_service.compute_aggregate(ctx.root, plan, plan_ref, c)
                run_refs = [r for rid in dict.fromkeys(x for row in agg.rows for x in row.run_ids)
                            if (r := ctx.archive.latest(f"runs/{rid}"))]
                base = f"artifacts/aggregates/{c.id}"
                ctx.archive.put(f"{base}.json", "aggregate", agg.model_dump_json(indent=2) + "\n",
                                parents=[plan_ref, *run_refs], producer="agent:experiment/aggregate")
                ctx.archive.put(f"{base}.csv", "aggregate", run_service.aggregate_csv(agg),
                                producer="agent:experiment/aggregate")
                ctx.archive.put(f"{base}.md", "aggregate", run_service.aggregate_markdown(agg),
                                producer="agent:experiment/aggregate")
            a["aggregated_for"] = plan_ref.sha256
            a["comparisons"] = [c.id for c in comps]
            a.pop("log_done", None)
            return Continue("汇总完成")

        log_id = f"logs/round_{s.get('round', 1)}.md"
        if not a.get("log_done"):
            ctx.progress("实验（假实现）：撰写过程日志", 2, 3, "steps")
            md = self._render_log(ctx, s, a)
            agg_refs = [r for c in a["comparisons"] if (r := ctx.archive.latest(f"artifacts/aggregates/{c}.json"))]
            prev = ctx.archive.latest(log_id)
            ctx.archive.put(log_id, "process_log", md, parents=[r for r in (prev, *agg_refs) if r],
                            producer="agent:experiment/analysis", note=(a.get("notes") or ["初版"])[-1])
            a["log_done"] = True
            ctx.events.append("stage.decision", "（假实现）分析决定：stop_and_write（计划内实验全部完成）",
                              actor="agent:experiment", data={"decision": "stop_and_write"})
            return Continue("过程日志已保存")

        ctx.progress("实验（假实现）：等待审批过程日志", 3, 3, "steps")
        log_ref = ctx.archive.latest(log_id)
        assert log_ref is not None
        extra = [r for c in a["comparisons"] if (r := ctx.archive.latest(f"artifacts/aggregates/{c}.json"))]
        skipped = [k for k, t in s.get("tasks", {}).items() if t["status"] == "skipped"]
        return NeedsApproval(
            target=log_ref, kind="log",
            summary=f"第 {s.get('round', 1)} 轮总结（假实现）：计划内实验全部完成"
                    + (f"，跳过 {len(skipped)} 个失败组合" if skipped else "") + "；建议进入论文撰写",
            impact="批准后进入论文撰写（Writing）",
            extra_refs=extra,
        )

    def _render_log(self, ctx: StageContext, s: dict, a: dict) -> str:
        lines = [f"# 第 {s.get('round', 1)} 轮过程日志（假实现）", "", "## 观察", ""]
        for cid in a["comparisons"]:
            p = ctx.root / "artifacts" / "aggregates" / f"{cid}.md"
            if p.exists():
                lines += [p.read_text(encoding="utf-8"), ""]
        lines += ["## 解释", ""]
        for cid in a["comparisons"]:
            if cid == "all":
                continue
            try:
                agg = run_service.load_aggregate(ctx.root, cid)
            except Exception:
                continue
            for c in agg.contrasts:
                level = "supported_by_experiment" if (c.p_value is not None and c.p_value < 0.05) else "correlational"
                lines.append(f"- {cid}：{c.a} 相对 {c.b} 的 {c.metric} 均值差为 {c.diff_mean:+.4f}"
                             f"（Welch p = {c.p_value}），证据等级：{level}。")
        skipped = [k for k, t in s.get("tasks", {}).items() if t["status"] == "skipped"]
        lines += ["", "## 异常与处置", f"- 跳过的失败组合：{skipped or '无'}", "",
                  "## 决定与理由",
                  "- stop_and_write：计划内实验全部完成（停止条件 1）。“得到正面结果”不是停止条件。", "",
                  "## 预计成本", "- 进入写作阶段，不再提交新运行。"]
        if a.get("notes"):
            lines += ["", "## 修订说明", *[f"- {n}" for n in a["notes"]]]
        return "\n".join(lines) + "\n"


def create_stage() -> FakeExperimentStage:
    return FakeExperimentStage()
