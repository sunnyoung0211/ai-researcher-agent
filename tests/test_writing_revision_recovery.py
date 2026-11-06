"""Real revision dispatch repairs failed payloads; legacy attempts keep their bounds."""

import json

import pytest

from airesearcher.engine.stage import Continue
from airesearcher.stages.writing import review as r
from airesearcher.stages.writing import stage
from scripts.writing.demo import SampleWritingLLM, context_of, prepare_sample, run

from .test_writing_attempts import until_question
from .test_writing_review import revision_case, unit
from .test_writing_stage import receipt, restart, to_phase


def identifier_case():
    claim, context, placeholders, raw = revision_case()
    sentence = "On SST-2 using GPT-4o, " + context["sentence"]
    context["sentence"] = raw["sentence"] = sentence
    raw["replacement"] = "On SST-2 using GPT-4o, mean was [[VALUE_1]] within the evaluated setting."
    return claim, context, placeholders, raw


def test_existing_numeric_names_are_preserved_without_exempting_new_numbers():
    claim, context, placeholders, raw = identifier_case()
    text, revised = r.validate_revision(context, placeholders, raw, claim)
    assert "SST-2" in text and "GPT-4o" in text
    assert revised.values == claim.values and revised.evidence == claim.evidence


@pytest.mark.parametrize("replacement", [
    "On SST-3 using GPT-4o, mean was [[VALUE_1]].",
    "On SST-20 using GPT-4o, mean was [[VALUE_1]].",
    "On SST-2 and SST-2 using GPT-4o, mean was [[VALUE_1]].",
    "On SST-2-v99 using GPT-4o, mean was [[VALUE_1]].",
    "On SST-2 using GPT-4o, mean was [[VALUE_1]] and accuracy was 99.5.",
    "On SST-2 using GPT-4o, mean was [[VALUE_1]] across three runs.",
    "On SST-2 using GPT-4o, mean was [[VALUE_1]] across twenty-one runs.",
])
def test_name_exemption_cannot_hide_changed_names_or_new_quantities(replacement):
    claim, context, placeholders, raw = identifier_case()
    raw["replacement"] = replacement
    with pytest.raises(ValueError):
        r.validate_revision(context, placeholders, raw, claim)


def test_repair_includes_checked_replacement_and_actual_current_error():
    claim, context, placeholders, raw = identifier_case()
    context["issues"] = [{"code": "MISSING_LIMITATION", "detail": "Small sample.",
                          "suggestion": "Mention three runs."}]
    raw["replacement"] = context["sentence"] + " Across three runs."
    previous = {"response": raw, "reason": "revision may not add commands, citations, figures or raw numbers"}
    feedback = r.correction_context(context, previous, 3, claim=claim, placeholders=placeholders)
    assert feedback["previous"] == raw and feedback["attempt_number"] == 3
    assert set(feedback["preserved_identifiers"]) == {"SST-2", "GPT-4o"}
    assert any("spelled-out numeric" in error for error in feedback["errors"])
    assert any("limited number of runs" in action for action in feedback["repair_actions"])
    assert feedback["required_placeholder_counts"] == {"[[VALUE_1]]": 1}
    example = feedback["validated_response_examples"][0]
    assert "SST-2" in example["replacement"] and "limited number of runs" in example["replacement"]
    r.validate_revision(context, placeholders, example, claim)


def test_unbound_unsupported_sentence_can_be_removed_including_its_dataset_name():
    target = unit(None, "abstract")
    target["sentence"] = "On SST-2 our method dominates every baseline."
    context, placeholders = r.revision_context(target, [], None)
    raw = {"sentence": context["sentence"], "claim_id": None, "replacement": "", "hedge": "none"}
    assert r.validate_revision(context, placeholders, raw, None) == ("", None)


def forbid_external(monkeypatch):
    from airesearcher.llm.gateway import LiteLLMBackend
    from airesearcher.runtime.local import LocalExecutor

    def forbidden(*args, **kwargs):
        pytest.fail("Semantic recovery must use FakeLLM without training or real API requests")

    monkeypatch.setattr(LiteLLMBackend, "call", forbidden)
    monkeypatch.setattr(LocalExecutor, "submit", forbidden)


@pytest.mark.parametrize("failures", [1, 2])
def test_revision_retry_uses_actual_edits_and_a_new_request_after_restart(tmp_path, monkeypatch, failures):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "correction", compile_enabled=False, evidence_check=True)
    e.llm.backend.semantic_revision = True
    original = SampleWritingLLM.call
    bad, seen = [], []

    def correct(self, request):
        response = original(self, request)
        if request.prompt_id == r.REVISE_PROMPT:
            context = context_of(request)
            seen.append(context)
            if len(seen) <= failures:
                data = json.loads(response.text)
                data["replacement"] += " An accuracy of 99.5 was observed."
                bad.append(data)
            else:
                feedback = context["correction"]
                assert feedback["previous"] == bad[-1]
                assert feedback["attempt_number"] == failures + 1
                assert any("Remove added numbers" in action for action in feedback["repair_actions"])
                # Produce the correction from the supplied repair, not from a saved good response.
                data = feedback["validated_response_examples"][0]
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", correct)
    if failures == 2:
        q = until_question(p, e)
        assert len(seen) == 2
        e = restart(p, e)
        e.tick()
        assert len(seen) == 2
        p.questions.answer(q.question_id, "retry", request_id=q.question_id)
        e = restart(p, e)
    else:
        to_phase(e, "revise")
        assert isinstance(e.tick().result, Continue)
        e = restart(p, e)
    run(e)
    assert len(seen) == failures + 1
    journal = next((p.root / "paper/.writing").glob("revision-*-0-attempts.json"))
    attempts = json.loads(journal.read_text(encoding="utf-8"))["attempts"]
    assert [a["status"] for a in attempts] == ["rejected"] * failures + ["accepted"]
    assert all(a.get("call_id") and a.get("raw") for a in attempts)


def test_reviewer_retry_also_receives_field_repairs_not_only_an_error(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "review-correction", compile_enabled=False, evidence_check=True)
    original = SampleWritingLLM.call
    seen = []

    def correct(self, request):
        response = original(self, request)
        if (request.prompt_id == r.REVIEW_PROMPT
                and (not seen or context_of(request)["sentence"] == seen[0]["sentence"])):
            context = context_of(request)
            seen.append(context)
            data = json.loads(response.text)
            if len(seen) == 1:
                data["claim_id"] = "fabricated"
            else:
                feedback = context["correction"]
                assert feedback["previous"]["claim_id"] == "fabricated"
                assert feedback["repair_actions"] and feedback["allowed_issue_codes"]
                data = {**feedback["previous"], **feedback["required_identity"]}
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", correct)
    run(e)
    assert len(seen) == 2 and seen[1]["correction"]["attempt_number"] == 2


def test_legacy_pause_keeps_accepted_prefix_and_old_attempt_budget(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "legacy", compile_enabled=False, evidence_check=True)
    e.llm.backend.semantic_revision = True
    p.config.prompt_pins[r.REVISE_PROMPT] = 1
    p.save_config(p.config)
    original = SampleWritingLLM.call
    seen = []
    prefix = ("Our method dominates every evaluated baseline.", "Our method wins under all possible conditions.")

    def scripted(self, request):
        response = original(self, request)
        context = context_of(request)
        data = json.loads(response.text)
        if request.prompt_id == "writing/section" and context["section"] == "abstract":
            data["latex"] += "\n" + " ".join(prefix)
        if request.prompt_id == r.REVIEW_PROMPT and context["sentence"] in prefix:
            data["issues"] = [{"code": "NO_EVIDENCE", "detail": "Unbound result.",
                               "suggestion": "Remove unsupported assertion."}]
        if request.prompt_id == r.REVISE_PROMPT:
            seen.append(context)
            if context["claim_id"] is None:
                data.update(replacement="", hedge="none")
            elif "correction" in context and context["correction"]["attempt_number"] == 3:
                data = context["correction"]["validated_response_examples"][0]
            else:
                data["replacement"] += " An accuracy of 99.5 was observed."
        response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", scripted)
    original_route = stage.WritingStage._semantic_route

    def old_policy(self, *args):
        return {**original_route(self, *args), "policy": "old-validation-policy"}

    with monkeypatch.context() as old:
        old.setattr(stage.WritingStage, "_semantic_route", old_policy)
        q = until_question(p, e)
    assert len(seen) == 4  # Two accepted prose edits and two failures of the third target.
    old_name = q.text.split("Semantic unit ", 1)[1].split(" exhausted", 1)[0]
    transaction_name = old_name.rsplit("-2-attempts", 1)[0]
    assert transaction_name != old_name
    first = (p.root / "paper/review/review_v1.json").read_bytes()
    accepted = receipt(p, transaction_name)["responses"]
    assert len(accepted) == 2
    old_attempts = receipt(p, old_name)["attempts"]
    p.config.prompt_pins.pop(r.REVISE_PROMPT)
    p.save_config(p.config)
    reviews = sum(c.prompt_id == r.REVIEW_PROMPT for c in e.llm.backend.calls)
    p.questions.answer(q.question_id, "retry", request_id=q.question_id)
    e = restart(p, e)
    assert isinstance(e.tick().result, Continue)
    assert len(seen) == 5  # Only one new revision request, not a replay of the accepted prefix.
    assert sum(c.prompt_id == r.REVIEW_PROMPT for c in e.llm.backend.calls) == reviews
    assert receipt(p, transaction_name)["responses"][:2] == accepted
    journal = receipt(p, old_name)
    assert journal["attempts"][:2] == old_attempts and journal["attempts"][2]["number"] == 3
    assert journal["answers"] == [q.question_id]
    assert list((p.root / "paper/.writing").glob("semantic-policy-*.json"))
    run(e)
    assert (p.root / "paper/review/review_v1.json").read_bytes() == first


def test_incompatible_review_response_cannot_use_policy_recovery(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "bad-review", compile_enabled=False, evidence_check=True)
    e.llm.backend.semantic_revision = True
    to_phase(e, "revise")
    s = e.ck.scratch["writing"]
    path = p.root / f"paper/.writing/semantic-{s['semantic_basis']}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["responses"][0]["response"]["sentence"] = "not the original sentence"
    path.write_text(json.dumps(saved), encoding="utf-8")
    original_route = stage.WritingStage._semantic_route

    def changed_policy(self, *args):
        return {**original_route(self, *args), "policy": "changed-policy"}

    monkeypatch.setattr(stage.WritingStage, "_semantic_route", changed_policy)
    assert isinstance(e.tick().result, Continue)
    assert e.ck.scratch["writing"]["phase"] == "review"
    assert not list((p.root / "paper/.writing").glob("semantic-policy-*.json"))
