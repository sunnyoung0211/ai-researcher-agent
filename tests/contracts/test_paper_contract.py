"""论文提供的契约（详细设计 4 第 3、6、8 节）：图表、论断-证据矩阵、数字宏、核验报告。"""

from __future__ import annotations

import pytest

from airesearcher.core.errors import ValidationFailed
from airesearcher.services import evidence
from airesearcher.testing.contracts import Section, check_figures, check_paper


def test_figures_and_paper(sample_project):
    for fn in (check_figures, check_paper):
        s = Section(fn.__name__, "论文")
        fn(sample_project.root, s)
        assert not s.errors, (fn.__name__, s.errors)


def test_injected_wrong_claims_are_caught(sample_project):
    """样例论文里故意放了 2 条错误论断：一个数字对不上、一个结论与数据相反。"""
    report = evidence.check(sample_project.root)
    bad = {c.claim_id: {i.code for i in c.issues} for c in report.claims if c.issues}
    assert sorted(bad.values(), key=sorted) == [{"CONTRADICTED"}, {"NUM_MISMATCH"}]
    assert report.counts["blocker"] == 2
    good = [c for c in report.claims if not c.issues]
    assert good and all(c.support == "supported" for c in good)


def test_trace_of_a_claim(sample_project):
    trace = evidence.trace(sample_project.root, "CL1")
    assert trace.aggregates and trace.runs and trace.figures
    assert trace.aggregates[0]["rendered"] == trace.aggregates[0]["recomputed"]


def test_manuscript_with_blockers_needs_comment(sample_project):
    p = sample_project
    [a] = p.approvals.pending()
    assert a.kind == "manuscript"
    with pytest.raises(ValidationFailed):
        p.approvals.decide(a.approval_id, "approved", a.target.sha256, "", request_id="r1")
    assert p.approvals.decide(a.approval_id, "approved", a.target.sha256, "已知 2 个问题，先批准",
                              request_id="r2").status == "approved"


def test_undefined_number_macro_breaks_contract(sample_project):
    results = sample_project.root / "paper/sections/results.tex"
    results.write_text(results.read_text(encoding="utf-8") + "\nWe also get \\airval{C9.nope.score.mean}.\n",
                       encoding="utf-8")
    s = Section("paper", "论文")
    check_paper(sample_project.root, s)
    assert any("C9.nope.score.mean" in e for e in s.errors)
