"""Wrapper metrics originate only from tagged stdout, not arbitrary messages."""

import json

from airesearcher.core.models.run import RunState
from airesearcher.runtime.local import LocalExecutor

from .test_executor import _spec, _wait


def test_tagged_stdout_is_the_only_metric_source(project):
    ex = LocalExecutor(project)
    spec = _spec(project, ex, "r-metrics-protocol", {"method": "main", "seed": 0, "seconds": 0.01})
    ex.prepare(spec)
    ex.submit(spec)
    assert _wait(ex, spec.record.run_id).state == RunState.succeeded
    lines = (spec.run_dir / "metrics.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert rows and all(set(row) == {"seq", "ts", "name", "value", "split", "step"} for row in rows)
    stdout = (spec.run_dir / "stdout.log").read_text()
    assert len(rows) == sum(line.startswith("@@AIR_METRIC ") for line in stdout.splitlines())
