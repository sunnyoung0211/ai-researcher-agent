from __future__ import annotations

import time

from airesearcher.core.models.common import ProjectState as S


def drive(engine, until, max_ticks=200, sleep_waits=True, timeout=60.0):
    """同步地 tick 引擎，直到状态属于 until（单个状态或集合）。Wait 结果会真的等待（最多 0.5 秒）。"""
    until = {until} if isinstance(until, S) else set(until)
    t0 = time.time()
    for i in range(max_ticks):
        if i > 0 and engine.state in until:  # 至少 tick 一次，让引擎先处理刚写入的决定/回答
            return engine.state
        t = engine.tick()
        if t.kind == "step" and t.seconds and sleep_waits:
            time.sleep(min(t.seconds, 0.5))
        if time.time() - t0 > timeout:
            break
    raise AssertionError(f"engine stuck in {engine.state} (wanted {until}); reason={engine.ck.reason!r}")


def approve_pending(project, decision="approved", comment="", request_id=None):
    [a] = project.approvals.pending()
    return project.approvals.decide(a.approval_id, decision, a.target.sha256, comment,
                                    request_id=request_id or f"req-{a.approval_id}-{decision}")
