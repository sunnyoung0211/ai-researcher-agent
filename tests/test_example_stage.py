from __future__ import annotations

from airesearcher.core.models.common import ProjectState as S
from airesearcher.core.project import Project
from airesearcher.engine.engine import ProjectEngine
from airesearcher.testing.fake_llm import fake_gateway

from .helpers import approve_pending, drive


def _engine(project, script):
    return ProjectEngine(project, llm=fake_gateway(project, script), retry_delay=0)


def test_generate_reject_revise_approve(air_home):
    p = Project.create(goal="研究低资源下两种微调方法的差异和稳定性", task="tasks/smoke", home=air_home,
                       dev={"stage_impl": {"idea": "example"}})
    eng = _engine(p, {"example/summarize": [{"title": "初版标题", "points": ["a"]},
                                            {"title": "修改后标题", "points": ["b"]}]})
    drive(eng, S.IdeaPending)
    assert "初版标题" in (p.root / "example/summary.md").read_text()
    approve_pending(p, "changes_requested", "标题再具体一点")
    drive(eng, S.IdeaPending)
    text = (p.root / "example/summary.md").read_text()
    assert "修改后标题" in text
    v2 = p.archive.latest_version("example/summary.md")
    assert v2.ref.version == 2 and v2.note == "标题再具体一点"
    approve_pending(p)
    drive(eng, S.PlanDrafting)
    assert eng.ck.approved["idea"].version == 2
    calls = [line for line in (p.root / ".llm/calls.jsonl").read_text().splitlines() if line]
    assert len(calls) == 2


def test_question_then_answer(air_home):
    p = Project.create(goal="太短", task="tasks/smoke", home=air_home, dev={"stage_impl": {"idea": "example"}})
    eng = _engine(p, {"example/summarize": [{"title": "补充后的标题", "points": ["x"]}]})
    drive(eng, S.Paused)
    q = p.questions.pending()
    assert q is not None and q.allow_text and not q.options
    p.questions.answer(q.question_id, None, "想比较 LoRA 和全量微调在 SST-2 上的准确率", request_id="ans-1")
    drive(eng, S.IdeaPending)
    assert "LoRA" in (p.root / "example/summary.md").read_text()
    assert eng.ck.answer is None  # 回答只交给阶段一次
