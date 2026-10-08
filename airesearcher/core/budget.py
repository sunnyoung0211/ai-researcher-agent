"""预算记账（详细设计 1 第 4.3 节）。流水只追加到 budget.jsonl。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .events import Events
from .fsutil import append_jsonl, now, read_jsonl
from .models.budget import BudgetCategory, BudgetEntry, BudgetStatus
from .models.project import Limit

# 只统计、不设上限的类别也会出现在 status() 里
TRACKED = ("llm_usd", "llm_tokens", "llm_calls", "wall_hours", "cpu_hours", "gpu_hours", "storage_gb")


class Budget:
    def __init__(self, root: Path, events: Events, limits: Callable[[], dict[str, Limit]]):
        self.path = Path(root) / "budget.jsonl"
        self.events = events
        self._limits = limits  # 每次读最新的 project.yaml，用户在 GUI 调整上限后立即生效
        self._lock = threading.RLock()
        self._announced: dict[str, str] = {}  # 已写过 warning/exhausted 事件的类别，避免重复刷屏

    def charge(self, category: str, amount: float, source: str, data: dict | None = None) -> None:
        if amount == 0:
            return
        with self._lock:
            append_jsonl(
                self.path,
                BudgetEntry(ts=now(), category=category, amount=float(amount), source=source, data=data or {}),
            )

    def used(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for e in read_jsonl(self.path):
            totals[e["category"]] = totals.get(e["category"], 0.0) + float(e["amount"])
        return totals

    def status(self) -> BudgetStatus:
        used = self.used()
        limits = self._limits()
        cats: dict[str, BudgetCategory] = {}
        for name in sorted(set(TRACKED) | set(limits) | set(used)):
            lim = limits.get(name) or Limit()
            u = round(used.get(name, 0.0), 6)
            level = "ok"
            if lim.hard is not None and u >= lim.hard:
                level = "exhausted"
            elif lim.soft is not None and u >= lim.soft:
                level = "warn"
            cats[name] = BudgetCategory(used=u, soft=lim.soft, hard=lim.hard, level=level)
        return BudgetStatus(
            categories=cats,
            exhausted=any(c.level == "exhausted" for c in cats.values()),
            warn=any(c.level == "warn" for c in cats.values()),
        )

    def check(self) -> BudgetStatus:
        """同 status()；首次达到 soft/hard 时写 budget.warning / budget.exhausted 事件。"""
        st = self.status()
        for name, c in st.categories.items():
            prev = self._announced.get(name, "ok")
            if c.level == prev:
                continue
            if c.level == "warn" and prev == "ok":
                self.events.append(
                    "budget.warning", f"预算 {name} 已用 {c.used:g}，达到提醒线 {c.soft:g}", actor="system",
                    data={"category": name, "used": c.used, "soft": c.soft},
                )
            elif c.level == "exhausted":
                self.events.append(
                    "budget.exhausted", f"预算 {name} 已用 {c.used:g}，达到上限 {c.hard:g}", actor="system",
                    data={"category": name, "used": c.used, "hard": c.hard},
                )
            self._announced[name] = c.level
        return st
