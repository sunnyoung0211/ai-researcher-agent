"""Exercise task constraints, evidence uncertainty, bounded repairs, and selection recovery offline."""

import json

import pytest
from pydantic import ValidationError

from airesearcher.core.models.common import ProjectState as S
from airesearcher.engine.stage import Error, Stop
from airesearcher.stages.idea.demo import simulated_selection
from airesearcher.stages.idea.evidence import load_json
from airesearcher.stages.idea.models import ScoreOutput
from airesearcher.stages.idea.selection import (
    gap_table_step,
    normalize_idea,
    refine_step,
    score_step,
    selection_context,
    task_config,
)
from airesearcher.stages.idea.storage import save_json

from .helpers import drive
from .test_idea_merge import seed


def ready(air_home, **kwargs):
    project, engine, cards = seed(air_home, **kwargs)
    drive(engine, S.Paused)
    ctx = engine.build_context()
    save_json(ctx, "idea/brief.json", {"research_object": "LoRA", "task": "tasks/smoke",
                                      "target_variables": [], "data_or_workload": [], "constraints": {},
                                      "keywords": ["LoRA"], "missing_info": ["Confirm task readiness."]})
    return project, engine, ctx, cards


def draft(ctx):
    from airesearcher.core.models.idea import SelectedIdea
    return SelectedIdea.model_validate(simulated_selection(selection_context(ctx))["idea"])


def scores(evidence=()):
    return {name: {"score": 4, "rationale": "A bounded assessment based on the supplied evidence.",
                   "evidence": list(evidence), "to_verify": False}
            for name in ("novelty", "feasibility", "impact", "testability", "risk")}


def test_refinement_enforces_task_budget_and_removes_unregistered_citations(air_home):
    _, _, ctx, _ = ready(air_home)
    idea = draft(ctx)
    idea.task = {"task_config": "../../other-task"}
    idea.budget = {"llm_usd": 1000}
    idea.citations += ["arxiv:0000.00000"]
    idea.baselines[0].citations = ["s2:p0", "s2:invented"]
    idea.motivation += " Unsupported claim [s2:invented]."
    normalized = normalize_idea(ctx, idea, task_config(ctx), "supported")
    assert normalized.task["task_config"] == "tasks/smoke"
    assert normalized.budget["llm_usd"] == 5
    assert normalized.citations == ["s2:p0"]
    assert normalized.baselines[0].citations == ["s2:p0"]
    assert "[s2:invented]" not in normalized.motivation
    assert normalized.data == []
    assert any("TASK FIT" in q for q in normalized.open_questions)
    assert any("Unverified reference" in q for q in normalized.open_questions)


@pytest.mark.parametrize("mutation", [
    "unknown_metric", "direction", "duplicate_metric", "duplicate_id", "blank_falsifier",
])
def test_task_constraint_violations_are_rejected(air_home, mutation):
    _, _, ctx, _ = ready(air_home)
    idea = draft(ctx)
    if mutation == "unknown_metric":
        idea.metrics[0].name = "invented_accuracy"
    elif mutation == "direction":
        idea.metrics[0].direction = "lower"
    elif mutation == "duplicate_metric":
        idea.metrics.append(idea.metrics[-1].model_copy())
    elif mutation == "duplicate_id":
        idea.hypotheses.append(idea.hypotheses[0].model_copy())
    else:
        idea.hypotheses[0].falsified_if = " "
    with pytest.raises(ValueError):
        normalize_idea(ctx, idea, task_config(ctx))


def test_refine_recovery_does_not_repeat_calls(air_home):
    project, engine, ctx, _ = ready(air_home)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    count = len(engine.llm.backend.calls)
    original = (project.root / "idea/selected.json").read_bytes()
    state["phase"] = "refine"
    refine_step(ctx, state)
    assert len(engine.llm.backend.calls) == count
    assert (project.root / "idea/selected.json").read_bytes() == original
    assert project.archive.latest("idea/selected.json").version == 1


def test_invalid_metrics_allow_one_repair_only(air_home):
    _, engine, ctx, _ = ready(air_home)
    invalid = simulated_selection(selection_context(ctx))
    invalid["idea"]["metrics"][0]["name"] = "fake_metric"
    engine.llm.backend.script["idea/refine"] = [invalid]
    engine.llm.backend._fixed_prompts.add("idea/refine")
    state = engine.ck.scratch["idea"]
    assert isinstance(refine_step(ctx, state), Error)
    assert state["phase"] == "refine"
    assert sum(req.prompt_id == "idea/refine" for req in engine.llm.backend.calls) == 2
    assert isinstance(refine_step(ctx, state), Error)
    assert sum(req.prompt_id == "idea/refine" for req in engine.llm.backend.calls) == 2


def test_interrupted_refinement_requires_manual_usage_review(air_home):
    _, engine, ctx, _ = ready(air_home)
    from airesearcher.core.fsutil import sha256_bytes
    digest = sha256_bytes(json.dumps(selection_context(ctx), ensure_ascii=False, sort_keys=True).encode())
    save_json(ctx, "idea/selection_rounds/initial/refine_attempts.json",
              {"attempts": [{"status": "started"}], "input_sha256": digest})
    before = len(engine.llm.backend.calls)
    assert isinstance(refine_step(ctx, engine.ck.scratch["idea"]), Stop)
    assert len(engine.llm.backend.calls) == before


@pytest.mark.parametrize("value", [True, 0, 6, 3.0])
def test_scores_require_strict_integer_one_to_five(value):
    output = scores()
    output["risk"]["score"] = value
    with pytest.raises(ValidationError):
        ScoreOutput.model_validate(output)


def test_abstract_and_conflicted_evidence_cannot_be_promoted(air_home):
    _, engine, ctx, cards = ready(air_home, full=False)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    engine.llm.backend.script["idea/score"] = [scores([cards[0].card_id + "#0"])]
    engine.llm.backend._fixed_prompts.add("idea/score")
    score_step(ctx, state)
    result = load_json(ctx, "idea/scores.json")
    assert all(result[name]["to_verify"] for name in scores())
    assert "higher risk" in result["note"]


def test_fulltext_item_evidence_and_task_fit_are_distinguished(air_home):
    _, engine, ctx, cards = ready(air_home)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    engine.llm.backend.script["idea/score"] = [scores([cards[0].card_id + "#0"])]
    engine.llm.backend._fixed_prompts.add("idea/score")
    score_step(ctx, state)
    output = load_json(ctx, "idea/scores.json")
    assert not output["novelty"]["to_verify"]
    assert output["feasibility"]["to_verify"] and output["risk"]["to_verify"]


def test_unknown_scoring_evidence_is_rejected(air_home):
    _, engine, ctx, _ = ready(air_home)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    engine.llm.backend.script["idea/score"] = [scores(["invented-card#0"])]
    engine.llm.backend._fixed_prompts.add("idea/score")
    assert isinstance(score_step(ctx, state), Error)
    assert not ctx.archive.latest("idea/scores.json")


def test_gap_table_covers_citations_and_marks_unsupported_cells(air_home):
    project, engine, ctx, _ = ready(air_home)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    score_step(ctx, state)
    gap_table_step(ctx, state)
    table = load_json(ctx, "idea/gap_table.json")
    assert [r["paper_id"] for r in table["rows"]] == ["s2:p0"]
    assert all(c["text"] == "To verify" and c["to_verify"] for c in table["rows"][0]["cells"].values())
    assert "[TO VERIFY]" in (project.root / "idea/gap_table.md").read_text(encoding="utf-8")
    assert state["phase"] == "write"


def test_gap_table_rejects_cross_paper_evidence(air_home):
    _, engine, ctx, cards = ready(air_home, count=2)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    score_step(ctx, state)
    rows = [{"paper_id": pid, "cells": {name: {
        "text": "Claim", "evidence": [cards[-1].card_id + "#0"], "to_verify": False
    } for name in ("task_data", "method", "setting", "compute", "evaluation", "difference")}}
            for pid in ("s2:p0", "s2:p1")]
    engine.llm.backend.script["idea/gap_table"] = [{"rows": rows}]
    engine.llm.backend._fixed_prompts.add("idea/gap_table")
    assert isinstance(gap_table_step(ctx, state), Error)
    assert not ctx.archive.latest("idea/gap_table.md")


def test_saved_output_cannot_be_reused_with_different_evidence(air_home):
    _, engine, ctx, _ = ready(air_home)
    state = engine.ck.scratch["idea"]
    refine_step(ctx, state)
    old = load_json(ctx, "idea/brief.json")
    old["missing_info"].append("A newly supplied task constraint.")
    save_json(ctx, "idea/brief.json", old)
    count = len(engine.llm.backend.calls)
    assert isinstance(refine_step(ctx, state), Error)
    assert len(engine.llm.backend.calls) == count
