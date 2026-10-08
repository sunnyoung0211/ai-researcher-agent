"""启动核对（详细设计 1 第 5.6 节）。后台启动时对每个项目依次执行：

1. （本期没有 SQLite 索引，跳过重建）
2. 读取 checkpoint.json —— 由 ProjectEngine 构造时完成；
3. runs/*/status.json 中 queued / running / unknown 的运行逐个调用 executor.reconcile()，写 run.reconciled 事件；
4. 审批、问题文件已决定/回答但检查点未更新的 —— 由引擎每次 tick 的 _reconcile_files() 以文件为准补做；
5. 重新启动引擎线程，从检查点继续。不会重新提交已有运行。
"""

from __future__ import annotations

from typing import Any

from airesearcher.core.models.run import RunState
from airesearcher.core.project import Project
from airesearcher.services.runs import list_runs

RECONCILE_STATES = {RunState.created, RunState.preparing, RunState.queued, RunState.running, RunState.unknown}


def reconcile_runs(project: Project, executor: Any) -> list[tuple[str, str, str]]:
    """返回 [(run_id, 原状态, 核对后状态)]。"""
    out = []
    for rv in list_runs(project.root):
        before = rv.status.state
        if before not in RECONCILE_STATES:
            continue
        after = executor.reconcile(rv.record.run_id).state
        out.append((rv.record.run_id, before.value, after.value))
        project.events.append(
            "run.reconciled", f"重启核对运行 {rv.record.run_id}：{before.value} → {after.value}", actor="engine",
            run_id=rv.record.run_id, data={"before": before.value, "after": after.value},
        )
    return out
