"""实验提供的契约（详细设计 3 第 4、5、8 节）：计划、运行目录、汇总结果。"""

from __future__ import annotations

import json

from airesearcher.core.models.run import RunState
from airesearcher.services import runs as run_service
from airesearcher.testing.contracts import Section, check_aggregates, check_plan, check_runs


def _check(fn, root):
    s = Section(fn.__name__, "实验")
    fn(root, s)
    return s


def test_plan_runs_aggregates(sample_project):
    for fn in (check_plan, check_runs, check_aggregates):
        s = _check(fn, sample_project.root)
        assert not s.errors, (fn.__name__, s.errors)


def test_sample_runs_cover_success_failure_retry_trial(sample_project):
    runs = run_service.list_runs(sample_project.root)
    kinds = [r.record.kind for r in runs]
    states = [r.status.state for r in runs]
    assert kinds.count("trial") == 1
    assert states.count(RunState.failed) == 1
    assert sum(1 for r in runs if r.status.state == RunState.succeeded and r.record.kind != "trial") == 9
    retry = [r for r in runs if r.record.retry_of]
    assert len(retry) == 1 and retry[0].status.state == RunState.succeeded


def test_aggregates_exclude_failed_and_trial_runs(sample_project):
    agg = run_service.load_aggregate(sample_project.root, "all")
    reasons = {e["reason"].split(":")[0] for e in agg.excluded}
    assert {"failed", "kind=trial"} <= reasons
    assert all(r.n == 3 for r in agg.rows)
    # 论文引用数字的方式：(aggregate_id, group, metric, stat)
    v = run_service.resolve_value(sample_project.root, "C1", {"method": "main"}, "score", "mean")
    assert 0.7 < v < 0.85


def test_renamed_field_breaks_contract(sample_project):
    """有人悄悄把汇总结果里的 ci95 改名 → 论文阶段会读不出来；契约检查要能发现。"""
    p = sample_project.root / "artifacts/aggregates/C1.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for row in data["rows"]:
        row["conf_interval"] = row.pop("ci95")
    p.write_text(json.dumps(data), encoding="utf-8")
    s = _check(check_aggregates, sample_project.root)
    assert any("C1.json" in e and "Aggregate" in e for e in s.errors)


def test_missing_required_metric_breaks_contract(sample_project):
    run = next(r for r in run_service.list_runs(sample_project.root) if r.status.state == RunState.succeeded)
    (sample_project.root / "runs" / run.record.run_id / "metrics.jsonl").write_text("", encoding="utf-8")
    s = _check(check_runs, sample_project.root)
    assert any("缺少必需指标" in e for e in s.errors)
