"""Data preparation, changed-version questions and accepted input hashes."""

import sys
import time

from airesearcher.core.fsutil import sha256_file
from airesearcher.core.models.common import ProjectState as S
from airesearcher.core.models.plan import TaskConfig
from airesearcher.core.models.question import Answer
from airesearcher.runtime.data import ensure_data, input_refs

from .test_plan_stage import seeded


def context(air_home):
    _, engine, _, _ = seeded(air_home)
    ctx = engine.build_context()
    ctx.state = S.PlanDrafting
    return ctx


def task(base, prepare=None):
    return TaskConfig(
        name="data_fixture",
        domain="generic",
        description="Standard-library fixture",
        template_dir="tasks/smoke/template",
        entrypoint=["{python}", "run.py"],
        metrics=[],
        data=[
            {
                "name": "fixture",
                "source": "local:fixture",
                "files": [{"path": "values.txt", "sha256": None}],
                "prepare": prepare or [],
            }
        ],
    )


def test_existing_files_are_recorded_and_checked(air_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AIR_DATA_DIR", str(tmp_path))
    (tmp_path / "values.txt").write_text("version one")
    ctx = context(air_home)
    assert ensure_data(ctx, task(tmp_path)).ready
    assert input_refs(ctx.root)[0].sha256 == sha256_file(tmp_path / "values.txt")


def test_changed_bytes_require_explicit_acceptance(air_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AIR_DATA_DIR", str(tmp_path))
    path = tmp_path / "values.txt"
    path.write_text("one")
    ctx = context(air_home)
    t = task(tmp_path)
    assert ensure_data(ctx, t).ready
    path.write_text("two")
    status = ensure_data(ctx, t)
    assert not status.ready and {o.id for o in status.question.options} == {"accept", "end"}
    assert not any(o.default for o in status.question.options)
    # Match the engine's persisted question association.
    from airesearcher.core.fsutil import now

    ctx.scratch["plan"] = {"asked": "q-test"}
    ctx.answer = Answer(question_id="q-test", choice="accept", text="", answered_by="test", answered_at=now())
    assert ensure_data(ctx, t).ready
    assert input_refs(ctx.root)[0].sha256 == sha256_file(path)


def test_missing_files_prepare_asynchronously_and_failure_asks(air_home, tmp_path, monkeypatch):
    monkeypatch.setenv("AIR_DATA_DIR", str(tmp_path / "data"))
    ctx = context(air_home)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; p=Path(r'{data_dir}'); "
        "p.mkdir(parents=True,exist_ok=True); (p/'values.txt').write_text('ready')",
    ]
    t = task(tmp_path, command)
    status = ensure_data(ctx, t)
    assert status.waiting
    for _ in range(200):
        time.sleep(0.01)
        status = ensure_data(ctx, t)
        if not status.waiting:
            break
    assert status.ready
    assert (ctx.root / "environments/prepare_fixture.log").exists()
    (tmp_path / "data/values.txt").unlink()
    t.data[0].prepare = [sys.executable, "-c", "raise SystemExit(3)"]
    status = ensure_data(ctx, t)
    for _ in range(200):
        time.sleep(0.01)
        status = ensure_data(ctx, t)
        if not status.waiting:
            break
    assert status.question and {o.id for o in status.question.options} == {"retry", "end"}
