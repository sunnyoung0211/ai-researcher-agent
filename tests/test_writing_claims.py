"""Selectors are model intent; numerical values and identities are code-owned."""

import copy

import pytest

from airesearcher.stages.writing.bundle import build_bundle


@pytest.fixture
def bound_inputs(sample_project):
    from airesearcher.stages.writing.claims import load_figures

    p = sample_project
    refs = [p.archive.latest(f"artifacts/aggregates/{cid}.json") for cid in ("C1", "C2")]
    bundle = build_bundle(p.root, aggregate_refs=refs, include_runtime=True)
    figures = load_figures(p.root, {fid: p.archive.latest(f"artifacts/figures/{fid}") for fid in ("fig_c1", "fig_c2")})
    return p, bundle, figures


def valid_plan(bundle):
    claims = []
    for i, source in enumerate(bundle.aggregates):
        for contrast in source.data.contrasts:
            claims.append(
                {
                    "section": "results",
                    "text_template": "The observed mean difference is [[D]].",
                    "type": "comparative",
                    "statement_kind": "observation",
                    "hedge": "none",
                    "importance": "major",
                    "hypothesis": "H1" if i == 0 else None,
                    "values": [
                        {
                            "name": "D",
                            "aggregate_id": source.aggregate_id,
                            "group": {},
                            "metric": contrast.metric,
                            "stat": "diff_mean",
                            "contrast": {"a": contrast.a, "b": contrast.b},
                        }
                    ],
                    "comparison": {
                        "aggregate_id": source.aggregate_id,
                        "a": contrast.a,
                        "b": contrast.b,
                        "metric": contrast.metric,
                        "asserted": "a>b" if contrast.diff_mean > 0 else "a<b",
                    },
                    "evidence": [{"type": "aggregate", "ref": source.aggregate_id}],
                }
            )
    return {"claims": claims}


def test_bind_exact_values_and_macros(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan, claims_bytes, values_tex

    p, bundle, figures = bound_inputs
    claims, errors = bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert not errors and [c.claim_id for c in claims] == ["CL1", "CL2"]
    assert claims[0].values[0].aggregate_version == bundle.aggregates[0].ref.version
    assert claims[0].text.startswith(r"\claim{CL1}{") and r"\airval{" in claims[0].text
    assert claims[0].location is None
    assert claims_bytes(claims) == claims_bytes(claims)
    assert values_tex(claims) == values_tex(list(reversed(claims)))


def test_approved_dataset_identifier_is_not_a_measurement(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    bundle = bundle.model_copy(deep=True)
    bundle.idea.text += "\nApproved dataset: SST-2.\n"
    plan = valid_plan(bundle)
    plan["claims"][0]["text_template"] += r" We evaluate on \textsc{SST-2}."
    claims, errors = bind_plan(p.root, plan, bundle, figures)
    assert not errors
    assert "SST-2" in claims[0].text
    plan["claims"][0]["text_template"] += " Accuracy is 99.9 percent."
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert any("literal claim numbers" in e for e in errors)


def test_comparative_claim_explains_empty_values(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    plan["claims"][0].update(text_template="The first method performs better.", values=[])
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert any("nonempty values" in e for e in errors)


def test_plan_selector_reports_allowed_fields(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    plan["claims"][0]["evidence"].append(
        {"type": "plan", "ref": bundle.plan_ref.artifact_id, "selector": {"group": {}}}
    )
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert any("plan evidence selector" in e and "experiments" in e for e in errors)


def method_plan(bundle):
    plan = valid_plan(bundle)
    plan["claims"].append({
        "section": "method", "type": "method_fact", "statement_kind": "observation",
        "hedge": "none", "importance": "minor", "text_template": "The approved duration is [[SECONDS]] seconds.",
        "values": [], "comparison": None,
        "plan_parameters": [{"name": "SECONDS", "experiment_id": "E1", "parameter": "seconds"}],
        "evidence": [{"type": "plan", "ref": bundle.plan_ref.artifact_id, "selector": {"experiments": ["E1"]}}],
    })
    return plan


def test_configuration_numbers_come_from_exact_approved_plan(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = method_plan(bundle)
    # The unapproved working copy cannot supply new parameter values.
    current = p.root / bundle.plan_ref.artifact_id
    current.write_text(current.read_text(encoding="utf-8").replace("seconds: 1.0", "seconds: 999.0"), encoding="utf-8")
    claims, errors = bind_plan(p.root, plan, bundle, figures)
    assert not errors
    fact = claims[-1]
    assert "1.0 seconds" in fact.text and "999" not in fact.text
    assert fact.values == [] and "plan_parameters" not in fact.model_dump()
    link = next(e for e in fact.evidence if "Code-resolved" in e.note)
    assert link.version == bundle.plan_ref.version and '"parameter": "seconds"' in link.note


@pytest.mark.parametrize("damage", ["experiment", "parameter", "scope", "literal", "duplicate", "rendered", "results"])
def test_plan_parameter_does_not_bypass_evidence_checks(bound_inputs, damage):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = method_plan(bundle)
    fact = plan["claims"][-1]
    if damage == "experiment":
        fact["plan_parameters"][0]["experiment_id"] = "E999"
    elif damage == "parameter":
        fact["plan_parameters"][0]["parameter"] = "accuracy"
    elif damage == "scope":
        fact["evidence"][0]["selector"]["experiments"] = ["E2"]
    elif damage == "literal":
        fact["text_template"] += " It achieved accuracy 99.9."
    elif damage == "duplicate":
        fact["plan_parameters"].append(fact["plan_parameters"][0].copy())
    elif damage == "rendered":
        fact["plan_parameters"][0]["value"] = 999
    else:
        fact.update(section="results", type="numeric")
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert any(e.startswith("CL3:") for e in errors)


def test_actionable_correction_covers_each_bad_field(bound_inputs):
    from airesearcher.stages.writing.claims import correction_context

    p, bundle, figures = bound_inputs
    plan = method_plan(bundle)
    plan["claims"][0].update(text_template="The first method is better.", values=[])
    plan["claims"][-1].update(text_template="Training lasts 1.0 seconds.", plan_parameters=[])
    feedback = correction_context(p.root, plan, "failed", bundle, figures)
    repairs = {r["claim_id"]: r for r in feedback["repairs"]}
    assert repairs["CL1"]["path"] == "claims[0]"
    assert repairs["CL1"]["value_example"]["aggregate_id"] == "C1"
    comp = plan["claims"][0]["comparison"]
    assert repairs["CL1"]["value_example"]["contrast"] == {"a": comp["a"], "b": comp["b"]}
    assert repairs["CL3"]["parameter_examples"] == [{"name": "SECONDS", "experiment_id": "E1", "parameter": "seconds"}]
    assert len(feedback["required_results"]) == 2 and feedback["previous"] == plan


def test_schema_rejected_evidence_still_receives_repair_feedback(bound_inputs):
    from airesearcher.stages.writing.claims import correction_context

    p, bundle, figures = bound_inputs
    plan = method_plan(bundle)
    plan["claims"][-1]["evidence"] = 7
    feedback = correction_context(p.root, plan, "Schema rejected evidence", bundle, figures)
    assert any("CL3" in e for e in feedback["errors"])
    repair = next(r for r in feedback["repairs"] if r["claim_id"] == "CL3")
    assert repair["actions"] and repair["parameter_examples"]


@pytest.mark.parametrize(
    "field,value", [("rendered", "999"), ("key", "invented"), ("aggregate_version", 9), ("fmt", {"scale": 999})]
)
def test_planner_cannot_supply_trusted_value_fields(bound_inputs, field, value):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    plan["claims"][0]["values"][0][field] = value
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert errors


@pytest.mark.parametrize(
    "damage",
    [
        "group",
        "metric",
        "stat",
        "contrast",
        "direction",
        "coverage",
        "hypothesis",
        "literal",
        "results_interpretation",
        "unhedged",
    ],
)
def test_invalid_plan_fails_binding_or_coverage(bound_inputs, damage):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = copy.deepcopy(valid_plan(bundle))
    c = plan["claims"][0]
    v = c["values"][0]
    if damage == "group":
        v.update(stat="mean", contrast=None, group={"method": "missing"})
    elif damage in {"metric", "stat"}:
        v[damage] = "missing"
    elif damage == "contrast":
        v["contrast"] = None
    elif damage == "direction":
        c["comparison"]["asserted"] = "a<b"
    elif damage == "coverage":
        plan["claims"].pop()
    elif damage == "hypothesis":
        c["hypothesis"] = None
    elif damage == "literal":
        c["text_template"] = "The score is 99.9."
    elif damage == "results_interpretation":
        c.update(statement_kind="interpretation", hedge="possible")
    else:
        c.update(section="discussion", statement_kind="interpretation", hedge="none")
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert errors


@pytest.mark.parametrize("damage", ["unknown", "duplicate", "missing", "key", "direct", "wrong_section"])
def test_marker_insertion_rejects_untrusted_edits(bound_inputs, damage):
    from airesearcher.stages.writing.claims import bind_plan, insert_markers, validate_bound_section

    p, bundle, figures = bound_inputs
    claims, errors = bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert not errors
    text = "[[CLAIM:CL1]]\n[[CLAIM:CL2]]"
    if damage == "unknown":
        text += " [[CLAIM:CL99]]"
    elif damage == "duplicate":
        text += " [[CLAIM:CL1]]"
    elif damage == "missing":
        text = "[[CLAIM:CL1]]"
    elif damage == "key":
        text += r"\airval{invented}"
    elif damage == "direct":
        text = claims[0].text + " [[CLAIM:CL2]]"
    with pytest.raises(ValueError):
        insert_markers(text, claims, "discussion" if damage == "wrong_section" else "results")
    good = insert_markers("[[CLAIM:CL1]]\n[[CLAIM:CL2]]", claims, "results")
    validate_bound_section("Human context.\n" + good, claims, "results")
    with pytest.raises(ValueError, match="altered"):
        validate_bound_section(good.replace("observed", "invented"), claims, "results")


def test_macro_collision_and_comment_locations(bound_inputs, monkeypatch):
    from airesearcher.stages.writing import claims as module

    p, bundle, figures = bound_inputs
    claims, errors = module.bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert not errors
    for section in module.SECTIONS:
        path = p.root / f"paper/sections/{section}.tex"
        path.write_text(
            "% " + claims[0].text + "\n" + "\n".join(c.text for c in claims if c.section == section), encoding="utf-8")
    located = module.locate_claims(p.root, claims)
    assert located[0].location["line"] == 2
    monkeypatch.setattr(module, "value_key", lambda selector: "collision")
    _, errors = module.bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert any("collision" in err for err in errors)


@pytest.mark.parametrize(
    "kind,ref",
    [
        ("aggregate", "C99"),
        ("figure", "fig_outside"),
        ("run", "outside"),
        ("paper", "unknown"),
        ("plan", "other.md"),
        ("log", "other.md"),
    ],
)
def test_evidence_allowlists(bound_inputs, kind, ref):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    plan["claims"][0]["evidence"].append({"type": kind, "ref": ref})
    _, errors = bind_plan(p.root, plan, bundle, figures)
    assert any(e.startswith("CL1:") for e in errors)


def test_integer_n_and_fixed_p_format(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan

    p, bundle, figures = bound_inputs
    plan = valid_plan(bundle)
    claim = plan["claims"][0]
    claim["text_template"] += " n=[[N]], p=[[P]]."
    source = bundle.aggregates[0]
    claim["values"] += [
        {"name": "N", "aggregate_id": "C1", "group": source.data.rows[0].group, "metric": "score", "stat": "n"},
        {**claim["values"][0], "name": "P", "stat": "p_value"},
    ]
    bound, errors = bind_plan(p.root, plan, bundle, figures)
    assert not errors
    n, prob = bound[0].values[1:]
    assert n.rendered == "3" and n.fmt["scale"] == 1 and n.fmt["decimals"] == 0
    assert prob.fmt == {"scale": 1, "decimals": 4, "suffix": ""}
    assert bound[0].values[0].fmt["scale"] == figures["fig_c1"].data.spec.scale


def test_negative_nonsignificant_coverage_is_mandatory(bound_inputs):
    from airesearcher.stages.writing.claims import bind_plan, coverage_errors

    p, bundle, figures = bound_inputs
    claims, errors = bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert not errors
    changed = bundle.model_copy(deep=True)
    changed.aggregates[1].data.contrasts[0].diff_mean = -0.1
    changed.aggregates[1].data.contrasts[0].p_value = 0.9
    assert any("C2" in e for e in coverage_errors(claims[:1], changed))
    assert not coverage_errors(claims, changed)


@pytest.mark.parametrize("definition", [r"\newcommand{\airval}[1]{0}", r"\def\claim#1#2{fake}", r"\input{air_values}"])
def test_template_reserved_macros_fail(bound_inputs, definition):
    from airesearcher.stages.writing.claims import SECTIONS, bind_plan, values_tex
    from airesearcher.stages.writing.writer import SectionOutput, Writer

    from .test_writing_stage import TEMPLATE, set_template

    p, bundle, figures = bound_inputs
    claims, errors = bind_plan(p.root, valid_plan(bundle), bundle, figures)
    assert not errors
    writer = Writer(p.root, bundle, llm=None, claims=claims)
    for section in SECTIONS:
        writer.write_section(
            SectionOutput(section=section, latex="Prose.\n" + "\n".join(c.text for c in claims if c.section == section))
        )
    (p.root / "paper/air_values.tex").write_bytes(values_tex(claims))
    set_template(p, {"main.tex": TEMPLATE.replace(r"\begin{document}", definition + r"\begin{document}")})
    from airesearcher.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="reserved|evidence"):
        writer.assemble_draft(template=p.config.template)
