"""Dependency readiness only: no model, executor, subprocess or training calls."""

import pytest

from airesearcher.stages.experiment.stage import ExperimentStage


def dependency_state():
    entries = {}
    for experiment, parents in {"E1": [], "E2": [], "E3": ["E1"], "E4": ["E2"]}.items():
        trial = {"experiment_id": experiment, "trial": True, "depends_on": parents, "state": "todo"}
        if not parents:
            trial.update(state="done", success_commit="approved-code")
        entries[f"trial-{experiment}"] = trial
        for seed in range(3):
            entries[f"{experiment}-seed={seed}"] = {
                "experiment_id": experiment, "trial": False, "depends_on": parents, "state": "todo",
            }
    return {"simulation": False, "commit": "approved-code", "entries": entries}


def test_upstream_formal_runs_unlock_before_dependent_trials():
    # Exact deadlock shape: E1/E2 trials succeeded, E3/E4 trials require their
    # upstream formal runs. Those six upstream runs must be schedulable now.
    state = dependency_state()
    ready = {key for key, entry in state["entries"].items()
             if entry["state"] == "todo" and ExperimentStage._ready(entry, state)}
    assert ready == {f"{experiment}-seed={seed}" for experiment in ("E1", "E2") for seed in range(3)}


def test_each_dependency_layer_keeps_its_own_trial_gate():
    state = dependency_state()
    entries = state["entries"]
    for seed in range(3):
        entries[f"E1-seed={seed}"]["state"] = "done"
    assert ExperimentStage._ready(entries["trial-E3"], state)
    assert not ExperimentStage._ready(entries["trial-E4"], state)
    assert not ExperimentStage._ready(entries["E3-seed=0"], state)
    entries["trial-E3"].update(state="done", success_commit=state["commit"])
    assert ExperimentStage._ready(entries["E3-seed=0"], state)
    assert entries["trial-E4"]["state"] == "todo"


def test_downstream_trial_waits_for_all_upstream_configurations():
    state = dependency_state()
    state["entries"]["E1-seed=0"]["state"] = "done"
    state["entries"]["E1-seed=1"]["state"] = "done"
    assert not ExperimentStage._ready(state["entries"]["trial-E3"], state)


@pytest.mark.parametrize("trial_state", ["todo", "running", "failed", "skipped", "blocked"])
def test_formal_run_never_bypasses_its_own_unsuccessful_trial(trial_state):
    state = dependency_state()
    state["entries"]["trial-E1"]["state"] = trial_state
    assert not ExperimentStage._ready(state["entries"]["E1-seed=0"], state)


@pytest.mark.parametrize("commit", [None, "older-code"])
def test_formal_run_requires_a_trial_for_the_current_code(commit):
    state = dependency_state()
    trial = state["entries"]["trial-E1"]
    if commit is None:
        trial.pop("success_commit")
    else:
        trial["success_commit"] = commit
    assert not ExperimentStage._ready(state["entries"]["E1-seed=0"], state)
