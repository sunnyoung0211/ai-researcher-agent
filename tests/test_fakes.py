"""测试替身：FakeExecutor（不启动进程的执行器）和 FakeLiterature（不联网的检索源）。"""

from __future__ import annotations

import pytest

from airesearcher.core.models.common import ProjectState as S
from airesearcher.core.models.literature import PaperRecord, SearchSnapshot
from airesearcher.core.models.run import RunState
from airesearcher.core.project import Project
from airesearcher.engine.engine import ProjectEngine
from airesearcher.services import runs as run_service
from airesearcher.testing.contracts import check_project
from airesearcher.testing.fake_executor import FakeExecutor, FakeOutcome
from airesearcher.testing.fake_literature import FakeLiterature

from .helpers import approve_pending, drive

QUERY = "parameter-efficient fine-tuning for low-resource text classification"


def make_project(air_home):
    return Project.create(goal="比较基线、主方法和消融在冒烟任务上的得分", task="tasks/smoke", home=air_home,
                          dev={"poll_seconds": 0, "compile": False})


def to_running(p, ex):
    eng = ProjectEngine(p, retry_delay=0, executor=ex)
    drive(eng, S.IdeaPending)
    approve_pending(p)
    drive(eng, S.PlanPending)
    approve_pending(p)
    return eng


def test_fake_literature_search():
    src = FakeLiterature()
    snap, papers = src.search(QUERY, year_from=2020, limit=5)
    SearchSnapshot.model_validate(snap.model_dump())
    assert 0 < len(papers) <= 5 and snap.paper_ids == [p.paper_id for p in papers]
    assert all(isinstance(p, PaperRecord) and p.year >= 2020 and p.snapshot_id == snap.snapshot_id
               and p.queries == [QUERY] and p.bibtex.startswith("@") for p in papers)
    assert "arxiv:2106.09685" in snap.paper_ids or "arxiv:2106.10199" in snap.paper_ids
    again, _ = src.search(QUERY, year_from=2020, limit=5)
    assert again.paper_ids == snap.paper_ids and again.snapshot_id == snap.snapshot_id  # 可重复
    assert src.search("quantum chromodynamics lattice")[1] == []  # 没有相关论文时返回空
    _, bert = src.search("BERT pretraining")
    assert {"arxiv:1810.04805", "doi:10.18653/v1/N19-1423"} <= {p.paper_id for p in bert}  # 预印本 + 正式版，用来测去重
    assert src.get("arxiv:2106.09685").citation_key == "hu2022lora" and src.get("doi:none") is None
    assert src.calls == [QUERY, QUERY, "quantum chromodynamics lattice", "BERT pretraining"]


def test_fake_literature_fixed_queries_and_failures(tmp_path):
    src = FakeLiterature(fail_queries={"boom"})
    with pytest.raises(ConnectionError):
        src.search("boom")
    src.fixed["my query"] = ["arxiv:2209.11055"]
    assert [p.title for p in src.search("my query")[1]] == ["Efficient Few-Shot Learning Without Prompts"]


def test_fake_executor_runs_instantly(air_home):
    p = make_project(air_home)
    ex = FakeExecutor(p)
    eng = to_running(p, ex)
    drive(eng, S.LogPending, timeout=30)
    runs = run_service.list_runs(p.root)
    assert len(runs) == 9 and all(r.status.state == RunState.succeeded for r in runs)
    assert len(ex.submitted) == 9 and ex.probe()["executor"] == "fake"
    for name in ("run.json", "status.json", "metrics.jsonl", "environment.json", "resources.json", "code"):
        assert (p.root / "runs" / runs[0].record.run_id / name).exists()
    by_kind = {}
    for r in runs:
        by_kind.setdefault(r.record.kind, []).append(run_service.load_run(p.root, r.record.run_id).metrics[0].value)
    mean = {k: sum(v) / len(v) for k, v in by_kind.items()}
    assert mean["main"] > mean["ablation"] > mean["baseline"]
    assert p.archive.latest(f"runs/{runs[0].record.run_id}") is not None  # 收集时已登记档案
    report = check_project(p.root, owners=["实验"])
    assert report.ok, report.text()


def test_fake_executor_failure_then_retry(air_home):
    p = make_project(air_home)

    def outcome(rec, config):  # 第一次运行 E2 的种子 1 失败，重试成功
        if rec.task_key.startswith("E2") and rec.seed == 1 and rec.retry_of is None:
            return FakeOutcome.fail(stderr="Traceback ...\nRuntimeError: CUDA out of memory")
        return FakeOutcome()

    ex = FakeExecutor(p, outcomes={"*": outcome})
    eng = to_running(p, ex)
    drive(eng, S.Paused, timeout=30)
    q = p.questions.pending()
    assert "CUDA out of memory" in q.text
    p.questions.answer(q.question_id, "retry", request_id="r1")
    drive(eng, S.LogPending, timeout=30)
    runs = run_service.list_runs(p.root)
    failed = [r for r in runs if r.status.state == RunState.failed]
    assert len(failed) == 1 and failed[0].status.failure_reason == "nonzero_exit"
    assert any(r.record.retry_of == failed[0].record.run_id and r.status.state == RunState.succeeded for r in runs)


def test_fake_outcome_matching(air_home):
    p = make_project(air_home)
    ex = FakeExecutor(p, outcomes={"E3": FakeOutcome(metrics={"score": 0.5}), "E1-seed=0": FakeOutcome()})
    eng = to_running(p, ex)
    drive(eng, S.LogPending, timeout=30)
    vals = {r.record.experiment_id: run_service.load_run(p.root, r.record.run_id).metrics[0].value
            for r in run_service.list_runs(p.root)}
    assert vals["E3"] == 0.5 and vals["E1"] != 0.5
