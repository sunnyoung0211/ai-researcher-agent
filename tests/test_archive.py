from __future__ import annotations

import pytest

from airesearcher.core.errors import PermissionDenied, ValidationFailed


def test_put_is_idempotent_and_versioned(project):
    a = project.archive
    r1 = a.put("idea/selected.md", "idea", "v1 内容", producer="agent:idea/writer")
    assert r1.version == 1
    assert a.put("idea/selected.md", "idea", "v1 内容") == r1  # 同内容不产生新版本
    r2 = a.put("idea/selected.md", "idea", "v2 内容", parents=[r1], note="修改")
    assert r2.version == 2
    assert a.latest("idea/selected.md") == r2
    assert a.read_text(r1) == "v1 内容"  # 历史版本可以取回
    assert (project.root / "idea/selected.md").read_text() == "v2 内容"
    assert [v.ref.version for v in a.versions("idea/selected.md")] == [1, 2]
    lin = a.lineage(r2)
    assert lin.parents[0].ref == r1
    types = [e.type for e in project.events.all()]
    assert types.count("artifact.created") >= 2


def test_get_detects_tampered_archive(project):
    a = project.archive
    r = a.put("plan/x.md", "plan", "hello")
    stored = a.stored_path(r)
    stored.write_text("tampered")
    with pytest.raises(ValidationFailed):
        a.get(r)


def test_paper_directory_copied_with_exclude(project):
    a = project.archive
    paper = project.root / "paper"
    (paper / "sections").mkdir(parents=True)
    (paper / "sections/intro.tex").write_text("intro v1")
    (paper / "build").mkdir()
    (paper / "build/main.pdf").write_bytes(b"%PDF-1")
    r1 = a.put("paper", "paper", paper, exclude=["build/**", "review/**"])
    # 只改 build/ 不产生新版本
    (paper / "build/main.pdf").write_bytes(b"%PDF-2")
    (paper / "review").mkdir()
    (paper / "review/review_v1.json").write_text("{}")
    assert a.put("paper", "paper", paper, exclude=["build/**", "review/**"]) == r1
    # 改章节产生新版本，旧版本目录树仍可取回
    (paper / "sections/intro.tex").write_text("intro v2")
    r2 = a.put("paper", "paper", paper, exclude=["build/**", "review/**"])
    assert r2.version == 2
    old = a.stored_path(r1)
    assert (old / "sections/intro.tex").read_text() == "intro v1"
    assert not (old / "build").exists()
    assert "sections/intro.tex" in a.get(r1).decode()


def test_runs_directory_manifest_only(project):
    a = project.archive
    run = project.root / "runs/r-1"
    run.mkdir(parents=True)
    (run / "metrics.jsonl").write_text('{"name":"score"}\n')
    r = a.put("runs/r-1", "run", run)
    info = a.version_info(r)
    assert info.stored_at is None and info.is_dir
    assert a.stored_path(r) == run.resolve()


def test_sync_detects_human_edit(project):
    a = project.archive
    r1 = a.put("idea/selected.md", "idea", "原文", producer="agent:idea")
    assert a.sync() == []
    (project.root / "idea/selected.md").write_text("用户改过")
    created = a.sync(["idea/selected.md"])
    assert len(created) == 1 and created[0].version == 2
    v2 = a.latest_version("idea/selected.md")
    assert v2.producer == "human-edited" and v2.parents == [r1]
    assert any(e.type == "artifact.human_edited" for e in project.events.all())


def test_put_rejects_escape(project):
    with pytest.raises(PermissionDenied):
        project.archive.put("../outside.md", "other", "x")
