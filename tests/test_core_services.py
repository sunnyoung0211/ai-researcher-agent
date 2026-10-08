from __future__ import annotations

import pytest

from airesearcher.core.errors import PermissionDenied, StaleApproval, StaleQuestion, ValidationFailed
from airesearcher.core.models import Question, QuestionOption
from airesearcher.core.models.project import Limit


def _submit(project, text="草稿"):
    ref = project.archive.put("idea/selected.md", "idea", text)
    return ref, project.approvals.request(ref, "idea", "摘要", "无下游影响")


def test_approval_decide_dedupe_and_stale(project):
    ref, aid = _submit(project)
    a = project.approvals.decide(aid, "approved", ref.sha256, "", request_id="req-1")
    assert a.status == "approved"
    # 同一 request_id 重复提交：原样返回
    again = project.approvals.decide(aid, "approved", ref.sha256, "", request_id="req-1")
    assert again.status == "approved" and again.request_ids == ["req-1"]
    # 不同 request_id 再次决定：已处理过 → 409
    with pytest.raises(StaleApproval):
        project.approvals.decide(aid, "rejected", ref.sha256, "x", request_id="req-2")
    assert len([e for e in project.events.all() if e.type == "approval.decided"]) == 1


def test_approval_superseded_by_new_version(project):
    ref1, aid1 = _submit(project, "v1")
    ref2 = project.archive.put("idea/selected.md", "idea", "v2")
    with pytest.raises(StaleApproval):
        project.approvals.decide(aid1, "approved", ref1.sha256, request_id="r")
    assert project.approvals.get(aid1).status == "superseded"
    aid2 = project.approvals.request(ref2, "idea", "s", "i")
    ref3 = project.archive.put("idea/selected.md", "idea", "v3")
    aid3 = project.approvals.request(ref3, "idea", "s", "i")
    assert project.approvals.get(aid2).status == "superseded"  # 新请求使旧的 pending 失效
    assert [a.approval_id for a in project.approvals.pending()] == [aid3]


def test_approval_wrong_expected_sha(project):
    ref, aid = _submit(project)
    with pytest.raises(StaleApproval):
        project.approvals.decide(aid, "approved", "0" * 64, request_id="r")


def test_manuscript_blocker_needs_comment(project):
    paper = project.root / "paper"
    (paper / "main.tex").write_text("x")
    ref = project.archive.put("paper", "paper", paper, exclude=["build/**", "review/**"])
    (paper / "review").mkdir()
    (paper / "review/review_v1.json").write_text('{"counts": {"blocker": 2}}')
    aid = project.approvals.request(ref, "manuscript", "s", "i")
    with pytest.raises(ValidationFailed):
        project.approvals.decide(aid, "approved", ref.sha256, "", request_id="r1")
    a = project.approvals.decide(aid, "approved", ref.sha256, "我知道这两个问题，先批准", request_id="r2")
    assert a.status == "approved"


def test_question_answer_validation(project):
    q = Question(text="怎么办？", options=[QuestionOption(id="skip", label="跳过", default=True),
                                       QuestionOption(id="end", label="结束项目")])
    qid = project.questions.ask(q, stage="experiment")
    assert project.questions.pending().question_id == qid
    with pytest.raises(ValidationFailed):
        project.questions.answer(qid, "nope", request_id="a")
    ans = project.questions.answer(qid, "skip", request_id="a1")
    assert ans.choice == "skip"
    assert project.questions.answer(qid, "skip", request_id="a1") == ans  # 去重
    with pytest.raises(StaleQuestion):
        project.questions.answer(qid, "skip", request_id="a2")
    assert project.questions.pending() is None


def test_text_only_question(project):
    qid = project.questions.ask(Question(text="请补充目标"), stage="example")
    with pytest.raises(ValidationFailed):
        project.questions.answer(qid, None, "", request_id="x")
    assert project.questions.answer(qid, None, "更多细节", request_id="y").text == "更多细节"


def test_budget_levels(project):
    project.config.budget["llm_usd"] = Limit(soft=1.0, hard=2.0)
    b = project.budget
    b.charge("llm_usd", 0.5, "test")
    assert b.check().categories["llm_usd"].level == "ok"
    b.charge("llm_usd", 0.6, "test")
    assert b.check().categories["llm_usd"].level == "warn"
    b.charge("llm_usd", 1.0, "test")
    st = b.check()
    assert st.exhausted and st.exhausted_categories() == ["llm_usd"]
    b.check()
    types = [e.type for e in project.events.all()]
    assert types.count("budget.warning") == 1 and types.count("budget.exhausted") == 1


def test_permissions(project):
    g = project.permissions.guard
    g("network", "api.semanticscholar.org")
    g("network", "https://export.arxiv.org/api/query")
    with pytest.raises(PermissionDenied):
        g("network", "evil.example.com")
    g("exec", "local")
    g("write", "src/run.py")
    with pytest.raises(PermissionDenied):
        g("write", "../../etc/passwd")
    with pytest.raises(PermissionDenied):
        g("write", "project.yaml", actor="agent:experiment/coder")
    with pytest.raises(PermissionDenied):
        g("write", "runs/r-1/metrics.jsonl", actor="agent:experiment/coder")
    g("write", "runs/r-1/metrics.jsonl", actor="executor")
    with pytest.raises(PermissionDenied):
        g("publish", "arxiv")
    granted = [e for e in project.events.all() if e.type == "permission.granted"]
    assert len({(e.data["action"], e.data["target"]) for e in granted}) == len(granted)


def test_workspace_git_tracks_only_src_and_configs(project):
    ws = project.workspace
    if not ws.has_git:
        pytest.skip("no git")
    (project.root / "src/run.py").write_text("print(1)")
    (project.root / "idea/notes.md").write_text("not tracked")
    sha = ws.commit("add code")
    assert sha and sha != "nogit"
    assert ws.commit("nothing changed") == sha
    out = project.root / "export"
    ws.export_src(sha, out)
    assert (out / "run.py").read_text() == "print(1)"
