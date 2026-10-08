"""文献提供的契约（详细设计 2 第 3、4、9 节）。真实实现完成后，用它的输出也跑一遍这些检查。"""

from __future__ import annotations

from airesearcher.core.models.idea import parse_selected_md
from airesearcher.services import literature
from airesearcher.testing.contracts import Section, check_literature


def test_literature_files(sample_project):
    s = Section("literature", "文献")
    check_literature(sample_project.root, s)
    assert not s.errors, s.errors
    assert s.checked >= 5


def test_every_citation_is_verifiable(sample_project):
    root = sample_project.root
    idea = parse_selected_md((root / "idea/selected.md").read_text(encoding="utf-8"))
    assert idea.primary_metric().name == "score"
    for pid in idea.citations:
        assert literature.verify_citation(root, pid).status == "verified"


def test_made_up_citation_is_rejected(sample_project):
    """“禁止编造引用”的落点：selected.md 引用了不在 literature.jsonl 中的论文 → 契约检查报错。"""
    sel = sample_project.root / "idea/selected.md"
    text = sel.read_text(encoding="utf-8")
    sel.write_text(text.replace("# 动机\n", "# 动机\n据说 [arxiv:9999.99999] 已经解决了这个问题。\n", 1),
                   encoding="utf-8")
    s = Section("literature", "文献")
    check_literature(sample_project.root, s)
    assert any("arxiv:9999.99999" in e for e in s.errors)
