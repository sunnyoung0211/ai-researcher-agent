"""Malformed immutable claims fail upstream; corrections actually receive repair inputs."""

import json
import re

import pytest

from airesearcher.stages.writing import claims as binding
from scripts.writing.demo import SampleWritingLLM, context_of, prepare_sample, run

from .test_writing_attempts import until_question
from .test_writing_claims import bound_inputs as bound_inputs
from .test_writing_claims import valid_plan
from .test_writing_stage import receipt, restart, to_phase, writing_calls


@pytest.fixture
def presentation_inputs(bound_inputs):
    p, bundle, figures = bound_inputs
    for figure in figures.values():
        figure.data.spec.ylabel, figure.data.spec.scale = "Score (dimensionless)", 1
    return p, bundle, figures


@pytest.mark.parametrize("template,reason", [
    ("The observed mean difference is [[D]]% compared to baseline.", "unescaped %"),
    (r"The observed mean difference is [[D]]\%.", "percent/percentage-point"),
    ("The observed mean difference is [[D]] percentage points.", "percent/percentage-point"),
    (r"The observed mean difference is \textbf{[[D]]}\%.", "percent/percentage-point"),
    ("The percentage-point difference is [[D]].", "percent/percentage-point"),
    ("The difference in time (percent) is [[D]].", "percent/percentage-point"),
    ("The observed mean difference is [[D]] seconds.", "unit 'seconds'"),
    ("The observed mean difference is [[D]] ms.", "unit 'ms'"),
    ("Training time was reduced by [[D]].", "signed difference"),
    ("The baseline was [[D]] higher than the other method.", "signed difference"),
    (r"The observed mean difference is \textbf{[[D]].", "unbalanced LaTeX braces"),
])
def test_invalid_claim_presentation_cannot_be_bound(presentation_inputs, template, reason):
    p, bundle, figures = presentation_inputs
    plan = valid_plan(bundle)
    plan["claims"][0]["text_template"] = template
    claims, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert any(e.startswith("CL1:") and reason in e for e in errors), errors
    assert not any(c.claim_id == "CL1" for c in claims)


def test_percent_display_requires_matching_scale_and_difference_is_not_relative(presentation_inputs):
    p, bundle, figures = presentation_inputs
    figure = figures["fig_c1"].data.spec
    figure.ylabel, figure.scale = "Score (%)", 100
    plan = valid_plan(bundle)
    plan["claims"][0]["text_template"] = "The observed mean difference is [[D]] percentage points."
    claims, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert not errors
    assert claims[0].values[0].fmt["scale"] == 100
    plan["claims"][0]["text_template"] = r"The observed mean difference is [[D]]\%."
    _, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert any("not a relative percent" in e for e in errors)
    value = plan["claims"][0]["values"][0]
    value.update(stat="mean", group=value["contrast"]["a"], contrast=None)
    plan["claims"][0]["text_template"] = r"The observed mean is [[D]]\%."
    _, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert not errors


def test_signed_subtraction_order_cannot_be_reversed_in_prose(bound_inputs):
    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    contrast = plan["claims"][0]["values"][0]["contrast"]
    a, b = (next(iter(contrast[g].values())) for g in ("a", "b"))
    plan["claims"][0]["text_template"] = f"The mean difference ({b} minus {a}) is [[D]]."
    _, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert any("written subtraction order" in e for e in errors)
    plan["claims"][0]["text_template"] = f"The mean difference ({a} minus {b}) is [[D]]."
    _, errors = binding.bind_plan(p.root, plan, bundle, figures)
    assert not errors


def test_claim_correction_contains_replacement_clauses_and_value_contracts(bound_inputs):
    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    plan["claims"][0]["text_template"] = "Time was reduced by [[D]]% compared to baseline."
    feedback = binding.correction_context(p.root, plan, "rejected", bundle, figures)
    repair = next(r for r in feedback["repairs"] if r["claim_id"] == "CL1")
    assert any("Repair text_template" in action for action in repair["actions"])
    contract = repair["value_contracts"][0]
    assert contract["text_template_example"] == "The observed mean difference is [[D]]."
    assert contract["ordered_contrast"] == plan["claims"][0]["values"][0]["contrast"]
    assert contract["format"]["scale"] == figures["fig_c1"].data.spec.scale
    assert contract["validated_figure_labels"] and contract["rendered"]
    assert any("unescaped %" in e for e in feedback["errors"])
    assert any("signed difference" in e for e in feedback["errors"])


def test_invalid_bound_claim_never_reaches_a_section_model(bound_inputs):
    from airesearcher.stages.writing.writer import Writer

    p, bundle, figures = bound_inputs
    claims, errors = binding.bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert not errors
    claims[0].section = "abstract"
    claims[0].text = r"\claim{CL1}{Invalid percent % hides closing brace}"

    class NoModel:
        def complete(self, **kwargs):
            pytest.fail("An immutable broken claim must be repaired before requesting section prose")

    writer = Writer(p.root, bundle, llm=NoModel(), claims=claims)
    with pytest.raises(ValueError, match="CL1 must be rebound"):
        writer.request_section("abstract")


def forbid_external(monkeypatch):
    from airesearcher.llm.gateway import LiteLLMBackend
    from airesearcher.runtime.local import LocalExecutor

    def forbidden(*args, **kwargs):
        pytest.fail("Writing recovery must not call real APIs or submit training")

    monkeypatch.setattr(LiteLLMBackend, "call", forbidden)
    monkeypatch.setattr(LocalExecutor, "submit", forbidden)


def correction_of(request):
    blocks = re.findall(r"<untrusted_document[^>]*>\s*(.*?)\s*</untrusted_document>",
                        request.messages[1]["content"], re.S)
    return json.loads(blocks[1]) if len(blocks) > 1 else None


def test_fake_claim_rewrites_actual_bad_template_before_any_sections(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "claim-repair", compile_enabled=False, evidence_check=True)
    original = SampleWritingLLM.call
    bad, seen = [], []

    def repair(self, request):
        response = original(self, request)
        if request.prompt_id == binding.PROMPT_ID:
            feedback = correction_of(request)
            seen.append(feedback)
            data = json.loads(response.text)
            if feedback is None:
                data["claims"][0]["text_template"] = "The method reduced time by [[D]]% (p=[[P]], n=[[N]])."
                bad.append(binding.ClaimPlan.model_validate(data).model_dump(mode="json"))
            else:
                assert feedback["previous"] == bad[0]
                repair = next(r for r in feedback["repairs"] if r["claim_id"] == "CL1")
                contract = next(c for c in repair["value_contracts"] if c["name"] == "D")
                # The replacement is built from the actual repair instruction, not a saved good reply.
                data = feedback["previous"]
                data["claims"][0]["text_template"] = contract["text_template_example"] + " p=[[P]], n=[[N]]."
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", repair)
    to_phase(e, "claims")
    e.tick()
    assert not writing_calls(e) and not (p.root / "paper/claims.jsonl").exists()
    e = restart(p, e)
    run(e)
    assert len(seen) == 2 and len(writing_calls(e)) == 8
    journal = next((p.root / "paper/.writing").glob("claim-plan-*-attempts.json"))
    assert [a["status"] for a in json.loads(journal.read_text(encoding="utf-8"))["attempts"]] == [
        "rejected", "accepted"]


def test_section_manual_retry_receives_failed_payload_repair_actions_and_fresh_key(tmp_path, monkeypatch):
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "section-repair", compile_enabled=False, evidence_check=True)
    original = SampleWritingLLM.call
    bad, contexts = [], []

    def repair(self, request):
        response = original(self, request)
        if request.prompt_id == "writing/section" and context_of(request)["section"] == "introduction":
            context = context_of(request)
            contexts.append(context)
            if len(contexts) > 1:
                correction = context["correction"]
                assert correction["previous"] == bad[-1]
                assert correction["attempt_number"] == len(contexts)
                assert any("Balance every" in action for action in correction["repair_actions"])
                assert "brace" in correction["errors"][0].lower()
            data = json.loads(response.text)
            if len(contexts) <= 2:
                data["latex"] += "\nAn unclosed {group."
                bad.append(data)
            else:
                data = context["correction"]["previous"]
                data["latex"] = data["latex"].replace("An unclosed {group.", "A balanced {group}.")
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", repair)
    q = until_question(p, e)
    assert len(contexts) == 2 and not (p.root / "paper/sections/introduction.tex").exists()
    e = restart(p, e)
    e.tick()
    assert len(contexts) == 2
    p.questions.answer(q.question_id, "retry", request_id=q.question_id)
    e = restart(p, e)
    run(e)
    assert len(contexts) == 3
    assert "A balanced {group}." in (p.root / "paper/sections/introduction.tex").read_text(encoding="utf-8")


def test_old_installed_invalid_claim_is_repaired_from_original_plan(tmp_path, monkeypatch):
    from airesearcher.stages.writing import stage

    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "old-claim-repair", compile_enabled=False, evidence_check=True)
    original_call = SampleWritingLLM.call
    def historical(self, request):
        response = original_call(self, request)
        if request.prompt_id == binding.PROMPT_ID:
            data = json.loads(response.text)
            abstract = {**data["claims"][0], "section": "abstract", "type": "qualitative",
                        "hypothesis": None, "comparison": None,
                        "text_template": "The method reduced time by [[D]]% (p=[[P]], n=[[N]])."}
            data["claims"].append(abstract)
            response.text = json.dumps(data)
        return response

    with monkeypatch.context() as old:
        old.setattr(stage.WritingStage, "_claim_basis", lambda *args: "old-binding-rules")
        old.setattr(binding, "latex_errors", lambda text: [])
        old.setattr(binding, "presentation_errors", lambda *args: [])
        old.setattr(SampleWritingLLM, "call", historical)
        q = until_question(p, e)
        assert "section-abstract" in q.text
        assert len(list((p.root / "paper/sections").glob("*.tex"))) == 7
    journal = p.root / "paper/.writing/claim-plan-old-binding-rules-attempts.json"
    original_bytes = journal.read_bytes()
    old_claims = (p.root / "paper/claims.jsonl").read_bytes()
    seen = []

    def repair(self, request):
        response = original_call(self, request)
        if request.prompt_id == binding.PROMPT_ID:
            feedback = correction_of(request)
            seen.append(feedback)
            assert feedback and "reduced time" in feedback["previous"]["claims"][2]["text_template"]
            assert any("unescaped %" in err for err in feedback["errors"])
            data = feedback["previous"]
            slot = next(r for r in feedback["repairs"] if r["claim_id"] == "CL3")
            contract = next(c for c in slot["value_contracts"] if c["name"] == "D")
            data["claims"][2]["text_template"] = contract["text_template_example"] + " p=[[P]], n=[[N]]."
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", repair)
    p.questions.answer(q.question_id, "retry", request_id=q.question_id)
    run(restart(p, e))
    assert len(seen) == 1 and journal.read_bytes() == original_bytes
    assert (p.root / "paper/claims.jsonl").read_bytes() != old_claims
    assert not binding.latex_errors((p.root / "paper/sections/results.tex").read_text(encoding="utf-8"))
    assert not binding.latex_errors((p.root / "paper/sections/abstract.tex").read_text(encoding="utf-8"))
    assert receipt(p, "bound-claims")["basis"] != "old-binding-rules"


def test_abstract_claim_correction_finishes_real_latex_compilation(tmp_path, monkeypatch):
    import shutil

    if not shutil.which("latexmk"):
        pytest.skip("Requires latexmk for the real compilation boundary")
    forbid_external(monkeypatch)
    p, e = prepare_sample(tmp_path / "compiled-abstract", compile_enabled=True, evidence_check=True)
    original = SampleWritingLLM.call
    seen = []

    def repair(self, request):
        response = original(self, request)
        if request.prompt_id == binding.PROMPT_ID:
            feedback = correction_of(request)
            seen.append(feedback)
            data = json.loads(response.text)
            if feedback is None:
                data["claims"].append({**data["claims"][0], "section": "abstract", "type": "qualitative",
                                       "hypothesis": None, "comparison": None,
                                       "text_template": "Training time fell by [[D]]% (p=[[P]], n=[[N]])."})
            else:
                data = feedback["previous"]
                repair = next(r for r in feedback["repairs"] if r["claim_id"] == "CL3")
                contract = next(c for c in repair["value_contracts"] if c["name"] == "D")
                data["claims"][2]["text_template"] = contract["text_template_example"] + " p=[[P]], n=[[N]]."
            response.text = json.dumps(data)
        return response

    monkeypatch.setattr(SampleWritingLLM, "call", repair)
    run(e)
    assert len(seen) == 2
    assert (p.root / "paper/build/main.pdf").read_bytes().startswith(b"%PDF-")
    report = json.loads((p.root / "paper/review/review_v1.json").read_text(encoding="utf-8"))
    assert report["compile_ok"] and report["counts"]["blocker"] == 0
