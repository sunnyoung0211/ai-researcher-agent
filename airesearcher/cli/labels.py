"""英文枚举 → 中文显示（与详细设计 5 第 7 节一致）。新增的枚举值显示原始英文，不报错。"""

from __future__ import annotations

STATE = {
    "IdeaDrafting": "选题中", "IdeaPending": "等待审批 idea", "PlanDrafting": "制定实验计划",
    "PlanPending": "等待审批计划", "Executing": "实验执行中", "Analyzing": "分析结果",
    "LogPending": "等待审批过程日志", "Writing": "撰写论文", "ManuscriptPending": "等待审批最终手稿",
    "Completed": "已完成", "Paused": "已暂停", "BudgetExhausted": "预算已用尽", "Failed": "出错停止",
}
STATE_COLOR = {
    "IdeaPending": "yellow", "PlanPending": "yellow", "LogPending": "yellow", "ManuscriptPending": "yellow",
    "Completed": "green", "Paused": "bright_black", "BudgetExhausted": "red", "Failed": "red",
}
RUN = {
    "created": "准备中", "preparing": "准备中", "queued": "排队中", "running": "运行中", "succeeded": "成功",
    "failed": "失败", "cancelled": "已取消", "unknown": "状态未知（需人工确认）",
}
FAILURE = {
    "nonzero_exit": "程序报错退出", "timeout": "超时", "cancelled": "已取消", "lost": "进程丢失（后台重启时发现）",
    "oom": "内存不足", "missing_metrics": "未输出必需指标", "wrapper_error": "运行包装器出错",
}
APPROVAL = {
    "pending": "待处理", "approved": "已批准", "changes_requested": "已退回", "rejected": "已拒绝",
    "superseded": "已被新版本取代",
}
KIND = {"idea": "idea", "plan": "实验计划", "log": "过程日志", "manuscript": "最终手稿"}
RUN_KIND = {
    "baseline": "基线", "main": "主方法", "ablation": "消融", "control": "对照", "replication": "复现",
    "exploration": "探索", "trial": "试运行", "smoke": "冒烟",
}
BUDGET = {
    "llm_usd": "大模型费用（美元）", "llm_tokens": "大模型 token", "llm_calls": "大模型调用次数",
    "wall_hours": "运行时长（小时）", "cpu_hours": "CPU 时间（小时）", "gpu_hours": "GPU 时间（小时）",
    "storage_gb": "存储（GB）",
}


def label(table: dict[str, str], value: str | None) -> str:
    if value is None:
        return ""
    return table.get(value, value)
