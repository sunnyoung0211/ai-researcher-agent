from __future__ import annotations

import time
import uuid

DEV = {"smoke_seconds": 0.3, "poll_seconds": 1, "compile": False}


def create(c, dev=None, idea="比较基线、主方法和消融在冒烟任务上的得分"):
    r = c.post("/api/projects", json={"idea_text": idea, "task": "tasks/smoke", "dev": {**DEV, **(dev or {})},
                                      "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()["project_id"]


def wait_state(c, pid, states, timeout=60):
    states = {states} if isinstance(states, str) else set(states)
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/api/projects/{pid}").json()
        if st["state"] in states:
            return st
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {states}; last={st['state']} {st['blocking_reason']}")


def decide(c, pid, decision="approved", comment="", request_id=None, sha=None):
    [a] = c.get(f"/api/projects/{pid}/approvals", params={"status": "pending"}).json()
    body = {"decision": decision, "comment": comment, "expected_sha256": sha or a["target"]["sha256"],
            "request_id": request_id or str(uuid.uuid4())}
    return a["approval_id"], c.post(f"/api/projects/{pid}/approvals/{a['approval_id']}/decision", json=body)


def wait_until(c, pid, predicate, timeout=60, what="condition"):
    """轮询项目状态，直到 predicate(status) 为真。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/api/projects/{pid}").json()
        if predicate(st):
            return st
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting for {what}; last={st['state']} {st['blocking_reason']}")
