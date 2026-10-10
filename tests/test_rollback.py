"""回滚到历史检查点（详细设计 1 第 5.7 节）。"""

from __future__ import annotations

import pytest

from airesearcher.core.errors import InvalidState, NotFound
from airesearcher.core.models.common import ProjectState as S
from airesearcher.core.project import Project
from airesearcher.engine import checkpoint as ckpt
from airesearcher.engine.engine import ProjectEngine
from airesearcher.testing.fake_executor import FakeExecutor

from .helpers import approve_pending, drive


def make(air_home):
    p = Project.create(goal="比较基线、主方法和消融在冒烟任务上的得分", task="tasks/smoke", home=air_home,
                       dev={"poll_seconds": 0, "compile": False})
    return p, ProjectEngine(p, retry_delay=0, executor=FakeExecutor(p))


def test_rollback_to_plan_drafting(air_home):
    p, eng = make(air_home)
    drive(eng, S.IdeaPending)
    approve_pending(p)
    drive(eng, S.PlanPending)
    milestones = [b for b in ckpt.briefs(p.root) if b.milestone]
    assert [b.state for b in reversed(milestones)][:4] == [S.IdeaDrafting, S.IdeaPending, S.PlanDrafting,
                                                           S.PlanPending]
    target = next(b.checkpoint_id for b in milestones if b.state == S.PlanDrafting)
    approve_pending(p)
    drive(eng, S.LogPending)
    assert set(eng.ck.approved) == {"idea", "plan"}

    eng.request_action("rollback", "rb-1", checkpoint_id=target)  # 等待审批日志时直接回滚
    assert eng.state == S.Paused and eng.ck.resume_to == S.PlanDrafting
    assert set(eng.ck.approved) == {"idea"} and eng.ck.pending_approval is None
    assert "已回滚到" in eng.ck.reason
    assert not p.approvals.pending()  # 当时待审的日志审批被取代
    ev = [e for e in p.events.all() if e.type == "project.rolled_back"]
    assert ev and ev[0].data["to"] == target
    assert len(list((p.root / "runs").iterdir())) == 9  # 不删除任何运行

    eng.request_action("resume", "rs-1")
    drive(eng, S.PlanPending)  # 从计划起草重新开始，重新提交计划审批
    [a] = p.approvals.pending()
    assert a.kind == "plan"


def test_rollback_rules(air_home):
    p, eng = make(air_home)
    drive(eng, S.IdeaPending)
    first = ckpt.briefs(p.root)[-1].checkpoint_id
    approve_pending(p)
    eng.tick()
    assert eng.state == S.PlanDrafting
    with pytest.raises(InvalidState, match="先暂停"):
        eng.request_action("rollback", checkpoint_id=first)
    eng.request_action("pause")
    with pytest.raises(NotFound):
        eng.request_action("rollback", checkpoint_id="ck-999999")
    eng.request_action("rollback", checkpoint_id=first)
    assert eng.state == S.Paused and eng.ck.resume_to == S.IdeaDrafting and eng.ck.approved == {}
