"""第二批 GUI 联调接口：SSE 全部消息类型、日志逐行推送、运行标签、模板、导出、回滚。"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from airesearcher.server.app import create_app
from airesearcher.server.sse import ProjectStream

from .api_helpers import create, decide, wait_state


@pytest.fixture
def client(air_home):
    with TestClient(create_app(home=air_home, engine_kwargs={"retry_delay": 0})) as c:
        yield c


def parse(messages):
    out = []
    for m in messages:
        fields = dict(line.split(": ", 1) for line in m.strip().splitlines())
        out.append((fields["event"], json.loads(fields["data"])))
    return out


def test_sse_message_types(client):
    c = client
    pid = create(c, dev={"smoke_seconds": 1.0})
    ps = ProjectStream(c.app.state.manager, pid)
    seen = parse(ps.poll())
    assert [e for e, _ in seen[:2]] == ["state", "budget"]  # 连接时先推完整状态和预算
    wait_state(c, pid, "IdeaPending")
    decide(c, pid)
    wait_state(c, pid, "PlanPending")
    decide(c, pid)
    t0 = time.time()
    while time.time() - t0 < 60:
        seen += parse(ps.poll())
        if c.get(f"/api/projects/{pid}").json()["state"] == "LogPending":
            break
        time.sleep(0.2)
    seen += parse(ps.poll())
    kinds = {e for e, _ in seen}
    assert {"state", "budget", "event", "approval", "run"} <= kinds
    approvals = [d for e, d in seen if e == "approval"]
    assert {"status": "pending"}.items() <= approvals[0].items()
    assert any(d["status"] == "approved" for d in approvals)
    run_status = {}
    for e, d in seen:
        if e == "run":
            run_status.setdefault(d["run_id"], []).append(d["status"])
    assert len(run_status) == 9
    assert all(s[-1] == "succeeded" for s in run_status.values())
    assert any("running" in s for s in run_status.values())  # 包装器写的状态变化也会推送
    # 断线重连：带上次的事件 seq，只补发之后的事件
    last = max(json.loads(m.split("data: ", 1)[1])["seq"] for m in ProjectStream(c.app.state.manager, pid).poll()
               if m.startswith("id:"))
    again = parse(ProjectStream(c.app.state.manager, pid, last_seq=last).poll())
    assert not [d for e, d in again if e == "event"]


def test_sse_derived_messages():
    from airesearcher.core.fsutil import now
    from airesearcher.core.models.event import Event
    from airesearcher.server.sse import derived

    def ev(type, data=None, run_id=None):
        return Event(seq=1, ts=now(), type=type, actor="x", summary="s", data=data or {},
                     run_id=run_id)

    assert derived(ev("question.asked", {"question_id": "q-1"})) == ("question", {"question_id": "q-1",
                                                                                  "status": "pending"})
    assert derived(ev("question.withdrawn", {"question_id": "q-1"}))[1]["status"] == "withdrawn"
    assert derived(ev("approval.decided", {"approval_id": "ap-1", "decision": "rejected"}))[1]["status"] == "rejected"
    assert derived(ev("run.labeled", {"label": "invalid"}, run_id="r-1"))[1] == {"run_id": "r-1",
                                                                                 "status": "labeled",
                                                                                 "label": "invalid"}
    assert derived(ev("stage.decision")) is None


class FakeLogs:
    """按调用次数返回日志的执行器替身：模拟“一行写了一半”和运行结束。"""

    def __init__(self, data: bytes, steps: list[tuple[int, bool]]):
        self.data, self.steps = data, steps  # 每次 logs() 时文件已写到第几个字节、运行是否已结束

    def logs(self, run_id, stream, offset):
        from airesearcher.runtime.executor import LogChunk

        size, ended = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        part = self.data[offset:size]
        end = offset + len(part)
        return LogChunk(text=part.decode("utf-8"), next_offset=end, eof=ended and end >= size)

    def status(self, run_id):
        from airesearcher.core.models.run import RunState, RunStatus

        return RunStatus(run_id=run_id, state=RunState.succeeded, host="h")


def test_log_stream_lines_and_resume():
    from airesearcher.server.sse import RunLogStream

    data = "第一行\nsecond li".encode() + b"ne\nlast"
    first = len("第一行\nsecond li".encode())
    ls = RunLogStream(FakeLogs(data, [(first, False), (len(data), True)]), "r-1")
    msgs, done = ls.poll()
    assert [d for _, d in parse(msgs)] == [{"text": "第一行"}] and not done  # 半行先不推
    msgs, done = ls.poll()
    assert parse(msgs) == [("line", {"text": "second line"}), ("line", {"text": "last"}),
                           ("end", {"run_id": "r-1", "status": "succeeded"})] and done
    # 断线重连：从第一行结束处继续，不重复
    resumed = RunLogStream(FakeLogs(data, [(len(data), True)]), "r-1", offset=len("第一行\n".encode()))
    assert [d["text"] for e, d in parse(resumed.poll()[0]) if e == "line"] == ["second line", "last"]


def test_log_stream_endpoint(client):
    c = client
    pid = create(c)
    wait_state(c, pid, "IdeaPending")
    decide(c, pid)
    wait_state(c, pid, "PlanPending")
    decide(c, pid)
    wait_state(c, pid, "LogPending", timeout=90)
    rid = c.get(f"/api/projects/{pid}/runs").json()[0]["run_id"]
    with c.stream("GET", f"/api/projects/{pid}/runs/{rid}/logs/stream") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = parse([m for m in body.split("\n\n") if m.strip()])
    assert events[-1] == ("end", {"run_id": rid, "status": "succeeded"})
    whole = c.get(f"/api/projects/{pid}/runs/{rid}/logs").json()["text"]
    assert [d["text"] for e, d in events if e == "line"] == whole.rstrip("\n").split("\n")
    assert c.get(f"/api/projects/{pid}/runs/r-nope/logs/stream").status_code == 404
