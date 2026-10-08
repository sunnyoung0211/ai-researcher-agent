"""检查点与重启恢复（详细设计 1 第 5.6 节、第 12.2 节）。"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from airesearcher.core.fsutil import read_json
from airesearcher.core.workspace import load_registry
from airesearcher.server.app import create_app

from .api_helpers import create, decide, wait_state


def _app(home, engines=True):
    return create_app(home=home, start_engines=engines, engine_kwargs={"retry_delay": 0})


def test_restart_while_approval_pending(air_home):
    with TestClient(_app(air_home)) as c:
        pid = create(c)
        st = wait_state(c, pid, "IdeaPending")
        aid = st["pending_approvals"][0]["approval_id"]
    root = load_registry(air_home)[pid]

    # 重启后待审批仍在
    with TestClient(_app(air_home, engines=False)) as c:
        st = c.get(f"/api/projects/{pid}").json()
        assert st["state"] == "IdeaPending" and st["pending_approvals"][0]["approval_id"] == aid
        # 模拟“审批写入文件后、检查点更新前崩溃”：引擎没在跑，决定只落在文件里
        _, r = decide(c, pid)
        assert r.status_code == 200
        ck = read_json(f"{root}/.state/checkpoint.json")
        assert ck["state"] == "IdeaPending" and ck["pending_approval"] == aid

    # 再次启动：按审批文件补做转换，且只做一次
    with TestClient(_app(air_home)) as c:
        wait_state(c, pid, "PlanPending")
        events = c.get(f"/api/projects/{pid}/events", params={"type": "state.changed"}).json()
        assert len([e for e in events if e["data"]["from"] == "IdeaPending"]) == 1


def test_restart_while_runs_running(air_home):
    with TestClient(_app(air_home)) as c:
        pid = create(c, dev={"smoke_seconds": 3})
        wait_state(c, pid, "IdeaPending")
        decide(c, pid)
        wait_state(c, pid, "PlanPending")
        decide(c, pid)
        t0 = time.time()
        while not c.get(f"/api/projects/{pid}").json()["active_runs"]:
            assert time.time() - t0 < 30
            time.sleep(0.1)
    # 后台已停：包装器是独立进程，运行继续
    root = load_registry(air_home)[pid]
    import pathlib

    statuses = [json.loads(p.read_text()) for p in pathlib.Path(root, "runs").glob("*/status.json")]
    running = [s["run_id"] for s in statuses if s["state"] in ("running", "queued")]
    assert running, statuses

    with TestClient(_app(air_home)) as c:
        events = c.get(f"/api/projects/{pid}/events", params={"type": "run.reconciled"}).json()
        assert {e["run_id"] for e in events} >= set(running)
        wait_state(c, pid, "LogPending", timeout=120)
        runs = c.get(f"/api/projects/{pid}/runs").json()
        assert len(runs) == 9  # 没有重复提交
        assert len({r["task_key"] for r in runs}) == 9
        assert {r["state"] for r in runs} == {"succeeded"}


def test_runs_finish_while_backend_down(air_home):
    with TestClient(_app(air_home)) as c:
        pid = create(c, dev={"smoke_seconds": 1})
        wait_state(c, pid, "IdeaPending")
        decide(c, pid)
        wait_state(c, pid, "PlanPending")
        decide(c, pid)
        t0 = time.time()
        while not c.get(f"/api/projects/{pid}").json()["active_runs"]:
            assert time.time() - t0 < 30
            time.sleep(0.1)
    root = load_registry(air_home)[pid]
    import pathlib

    def states():
        return {json.loads(p.read_text())["state"] for p in pathlib.Path(root, "runs").glob("*/status.json")}

    t0 = time.time()
    while states() & {"running", "queued", "preparing"}:
        assert time.time() - t0 < 30
        time.sleep(0.2)
    with TestClient(_app(air_home)) as c:
        events = c.get(f"/api/projects/{pid}/events", params={"type": "run.reconciled"}).json()
        assert events and all(e["data"]["after"] == "succeeded" for e in events)
        wait_state(c, pid, "LogPending", timeout=120)
        assert len(c.get(f"/api/projects/{pid}/runs").json()) == 9
