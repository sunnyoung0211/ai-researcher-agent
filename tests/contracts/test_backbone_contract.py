"""主干提供的契约：项目配置、事件、预算、审批、问题、检查点、产物存档。"""

from __future__ import annotations

from airesearcher.engine import checkpoint as ckpt
from airesearcher.testing.contracts import Section, check_backbone, check_project
from airesearcher.testing.sample import SAMPLE_PROJECT


def test_sample_project_passes_all_contracts():
    report = check_project(SAMPLE_PROJECT)
    assert report.ok, report.text()
    assert not any(s.skipped for s in report.sections)  # 样例项目覆盖了全部交接文件


def test_backbone_files(sample_project):
    s = Section("backbone", "主干")
    check_backbone(sample_project.root, s)
    assert not s.errors, s.errors


def test_sample_state_is_useful_for_gui(sample_project):
    p = sample_project
    ck = ckpt.load(p.root)
    assert ck.state.value == "ManuscriptPending"
    statuses = {(a.kind, a.status) for a in p.approvals.list()}
    assert {("idea", "changes_requested"), ("idea", "approved"), ("plan", "approved"), ("log", "approved"),
            ("manuscript", "superseded"), ("manuscript", "pending")} <= statuses
    [rec] = p.questions.records()
    assert rec.answer is not None and rec.answer.choice == "retry"


def test_tampered_archive_is_detected(sample_project):
    p = sample_project
    v = p.archive.latest_version("idea/selected.md")
    (p.root / v.stored_at).write_text("被改过的历史版本", encoding="utf-8")
    s = Section("backbone", "主干")
    check_backbone(p.root, s)
    assert any("存档校验失败" in e for e in s.errors)
