"""Numeric correction reaches the model; provider errors keep actionable recovery."""

import copy
import json

import pytest

from airesearcher.core.models.common import ProjectState as S
from airesearcher.engine.stage import Continue
from airesearcher.stages.writing import claim_feedback
from airesearcher.stages.writing import claims as binding
from scripts.writing.demo import SampleWritingLLM, context_of, prepare_sample, run

from .test_writing_attempts import until_question
from .test_writing_claim_presentation import correction_of, forbid_external
from .test_writing_claims import bound_inputs as bound_inputs
from .test_writing_claims import valid_plan
from .test_writing_stage import receipt, restart, to_phase, writing_calls


def with_threshold(plan, expression):
    plan = copy.deepcopy(plan)
    claim = plan["claims"][0]
    pvalue = copy.deepcopy(claim["values"][0])
    pvalue.update(name="P", stat="p_value")
    claim["values"].append(pvalue)
    claim["text_template"] = f"The observed mean difference is [[D]]; {expression} (p-value: [[P]])."
    return plan


@pytest.mark.parametrize("expression", ["p<0.05", "p <= 0.01", "alpha = 0.05", "p < 5e-2"])
def test_literal_cutoff_feedback_has_a_complete_validated_replacement(bound_inputs, expression):
    p, bundle, figures = bound_inputs
    bundle = bundle.model_copy(deep=True)
    bundle.idea.text += "\nDataset: SST-2.\n"
    plan = with_threshold(valid_plan(bundle), expression)
    plan["claims"][0]["text_template"] += " The approved dataset is SST-2."
    original = copy.deepcopy(plan)
    _, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert any("literal claim numbers" in error for error in errors)
    feedback = claim_feedback.correction_context(p.root, plan, "; ".join(errors), bundle, figures)
    repair = next(r for r in feedback["repairs"] if r["claim_id"] == "CL1")
    assert "2" not in repair["literal_numbers"] or expression == "p < 5e-2"
    assert any("cutoffs such as p<0.05" in action for action in repair["actions"])
    replacement = repair["replacement_claim_example"]
    assert "[[D]]" in replacement["text_template"] and "[[P]]" in replacement["text_template"]
    assert replacement["values"] == original["claims"][0]["values"]
    assert replacement["comparison"] == original["claims"][0]["comparison"]
    assert replacement["evidence"] == original["claims"][0]["evidence"]
    _, errors = binding.bind_plan(p.root, feedback["validated_response_example"], bundle, figures)
    assert not errors and feedback["remaining_errors_after_examples"] == []
    assert plan == original  # Guidance never rewrites the saved candidate in place.


def test_examples_do_not_hide_other_unresolved_claim_errors(bound_inputs):
    p, bundle, figures = bound_inputs
    plan = with_threshold(valid_plan(bundle), "p<0.05")
    plan["claims"][1]["evidence"] = []
    feedback = claim_feedback.correction_context(p.root, plan, "rejected", bundle, figures)
    assert feedback["remaining_errors_after_examples"]
    assert "validated_response_example" not in feedback


def test_fake_manual_retry_consumes_numeric_repair_and_keeps_every_slot(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "numeric-retry", compile_enabled=False, evidence_check=True)
    original = SampleWritingLLM.call
    seen, failed = [], []

    def correct(self, request):
        response = original(self, request)
        if request.prompt_id == binding.PROMPT_ID:
            correction = correction_of(request)
            seen.append(correction)
            if len(seen) <= 2:
                data = json.loads(response.text)
                data["claims"][0]["text_template"] += " The cutoff is p<0.05."
                failed.append(binding.ClaimPlan.model_validate(data).model_dump(mode="json"))
            else:
                assert correction["attempt_number"] == 3
                assert correction["previous"] == failed[-1]
                repair = next(r for r in correction["repairs"] if r["claim_id"] == "CL1")
                assert repair["literal_numbers"] == ["0.05"]
                data = correction["validated_response_example"]
                assert len(data["claims"]) == len(failed[-1]["claims"])
                assert data["claims"][0]["values"] == failed[-1]["claims"][0]["values"]
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", correct)
    q = until_question(p, e)
    assert len(seen) == 2 and not (p.root / "paper/claims.jsonl").exists()
    journal = next((p.root / "paper/.writing").glob("claim-plan-*-attempts.json"))
    before = json.loads(journal.read_text(encoding="utf-8"))["attempts"]
    e = restart(p, e)
    e.tick()
    assert len(seen) == 2  # A restart is not authorization for another paid attempt.
    p.questions.answer(q.question_id, "retry", request_id=q.question_id)
    e = restart(p, e)
    run(e)
    after = json.loads(journal.read_text(encoding="utf-8"))["attempts"]
    assert len(seen) == 3 and after[:2] == before
    assert [a["status"] for a in after] == ["rejected", "rejected", "accepted"]
    assert len(writing_calls(e)) == 8 and e.state == S.ManuscriptPending


class LocationDenied(Exception):
    status_code = 400


def test_provider_failure_shows_original_reason_and_preserves_completed_work(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "provider-failure", compile_enabled=False, evidence_check=True)
    original = SampleWritingLLM.call
    failures = []

    def unavailable(self, request):
        if request.prompt_id == "writing/section" and context_of(request)["section"] == "related_work":
            failures.append(request)
            if len(failures) <= 2:
                raise LocationDenied("Google AI Studio: User location is not supported for the API use.")
        return original(self, request)

    monkeypatch.setattr(SampleWritingLLM, "call", unavailable)
    to_phase(e, "write")
    e.tick()  # Introduction is durably accepted before the provider fails.
    introduction = (p.root / "paper/sections/introduction.tex").read_bytes()
    bound = receipt(p, "bound-claims")
    q = until_question(p, e)
    assert "HTTP 400" in q.text and "User location is not supported" in q.text
    assert "interrupted" not in q.text
    assert "available to your region" in q.text and "charges" in q.text
    assert len(failures) == 1 and not (p.root / "paper/sections/related_work.tex").exists()
    journal = next((p.root / "paper/.writing").glob("section-related_work-*-attempts.json"))
    saved = json.loads(journal.read_text(encoding="utf-8"))["attempts"]
    assert saved[0]["status"] == "call_failed" and saved[0]["http_status"] == 400
    assert "response" not in saved[0] and "unknown" in saved[0]["cost_status"]
    e = restart(p, e)
    e.tick()
    assert len(failures) == 1
    p.questions.answer(q.question_id, "retry", request_id=q.question_id)
    e = restart(p, e)
    q2 = until_question(p, e)
    assert q2.question_id != q.question_id and len(failures) == 2
    assert "HTTP 400" in q2.text
    p.questions.answer(q2.question_id, "retry", request_id=q2.question_id)
    e = restart(p, e)
    run(e)  # The fake provider becomes available; only then can generation continue.
    assert len(failures) == 3
    assert (p.root / "paper/sections/introduction.tex").read_bytes() == introduction
    assert receipt(p, "bound-claims")["basis"] == bound["basis"]
    assert sum(c.prompt_id == binding.PROMPT_ID for c in e.llm.backend.calls) == 1
    assert sum(context_of(c)["section"] == "introduction" for c in writing_calls(e)) == 1
    history = json.loads(journal.read_text(encoding="utf-8"))["attempts"]
    assert [a["status"] for a in history] == ["call_failed", "call_failed", "accepted"]
    assert all(a["number"] == i for i, a in enumerate(history, 1))
    assert e.state == S.ManuscriptPending


def test_numeric_feedback_does_not_invalidate_accepted_binding_policy(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    from airesearcher.stages.writing.stage import WritingStage

    p, e = prepare_sample(tmp_path / "same-policy", compile_enabled=False, evidence_check=True)
    to_phase(e, "write")
    basis = e.ck.scratch["writing"]["claim_basis"]
    monkeypatch.setattr(claim_feedback, "correction_context", lambda *args: pytest.fail("Accepted claims replanned"))
    assert isinstance(e.tick().result, Continue)
    assert e.ck.scratch["writing"]["claim_basis"] == basis
    assert isinstance(WritingStage().step(e.build_context()), Continue)
