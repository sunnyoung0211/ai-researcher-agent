"""Model intent -> exact frozen evidence. No Stage orchestration or semantic review."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from airesearcher.core.archive import Archive, dir_manifest, manifest_bytes
from airesearcher.core.errors import AirError
from airesearcher.core.fsutil import sha256_bytes
from airesearcher.core.models.claim import Claim, Comparison, EvidenceLink, Stat, ValueRef
from airesearcher.core.models.common import VersionRef
from airesearcher.core.models.figure import Figure
from airesearcher.core.models.plan import parse_plan_md
from airesearcher.services import evidence, runs

from ._latex import _is_escaped, active_text, commands, safe_path
from .bundle import EvidenceBundle

PROMPT_ID = "writing/claims"
SECTIONS = ("abstract", "introduction", "related_work", "method", "setup", "results", "discussion", "conclusion")
TOKEN = re.compile(r"\[\[([A-Za-z][A-Za-z0-9_]*)\]\]")
MARKER = re.compile(r"\[\[CLAIM:(CL[1-9][0-9]*)\]\]")
SAFE_KEY = re.compile(r"[A-Za-z0-9_.-]+")
IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b")
PREAMBLE = "\\input{air_values}\n\\newcommand{\\claim}[2]{#2}\n"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedContrast(StrictModel):
    a: dict
    b: dict


class PlannedValue(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    aggregate_id: str
    group: dict
    metric: str
    stat: Stat
    contrast: PlannedContrast | None = None


class PlannedEvidence(StrictModel):
    type: Literal["aggregate", "figure", "run", "paper", "plan", "log"]
    ref: str
    selector: dict | None = Field(
        default=None,
        description="Aggregate: group/metric/eval_condition only. Plan: experiments (exact IDs) only. Otherwise null.",
    )
    citation_scope: Literal["main_conclusion", "experimental_detail"] | None = None

    @model_validator(mode="after")
    def selector_fields(self):
        allowed = {"aggregate": {"group", "metric", "eval_condition"}, "plan": {"experiments"}}
        if set(self.selector or {}) - allowed.get(self.type, set()):
            fields = ", ".join(sorted(allowed.get(self.type, set()))) or "none (use null)"
            raise ValueError(f"{self.type} evidence selector allows only: {fields}")
        return self


class PlannedParameter(StrictModel):
    """A selector, never a model-supplied configuration value or result statistic."""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    experiment_id: str
    parameter: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Exact numeric key in the selected experiment's approved fixed configuration.",
    )


class PlannedComparison(StrictModel):
    aggregate_id: str
    a: dict
    b: dict
    metric: str
    asserted: Literal["a>b", "a<b", "a≈b"]


class PlannedClaim(StrictModel):
    section: Literal[
        "abstract", "introduction", "related_work", "method", "setup", "results", "discussion", "conclusion"
    ]
    text_template: str
    type: Literal["numeric", "comparative", "qualitative", "citation", "method_fact"]
    statement_kind: Literal["observation", "interpretation"]
    hedge: Literal["none", "likely", "possible"]
    importance: Literal["major", "minor"]
    hypothesis: str | None = None
    values: list[PlannedValue] = Field(
        default_factory=list,
        description="Measured-statistic placeholders. A comparison requires nonempty values from its own Aggregate.",
    )
    plan_parameters: list[PlannedParameter] = Field(
        default_factory=list,
        description="Method/setup configuration placeholders, resolved by code from the exact approved plan.",
    )
    comparison: PlannedComparison | None = None
    evidence: list[PlannedEvidence]

    @model_validator(mode="after")
    def comparison_values(self):
        if self.type == "comparative" and self.comparison is None:
            raise ValueError("comparative claim needs exact comparison")
        if self.comparison:
            if not self.values:
                raise ValueError("comparison requires nonempty values; "
                                 "bind a mean/diff_mean placeholder to its Aggregate")
            if {v.aggregate_id for v in self.values} != {self.comparison.aggregate_id}:
                raise ValueError("comparison values must all use comparison.aggregate_id; split different comparisons")
        if self.plan_parameters and (self.type != "method_fact" or self.section not in {"method", "setup"}):
            raise ValueError("plan_parameters are only for method_fact claims in method/setup, never measured results")
        return self


class ClaimPlan(StrictModel):
    claims: list[PlannedClaim]


class FigureSource(StrictModel):
    ref: VersionRef
    data: Figure


def load_figures(root: Path, refs: dict[str, VersionRef]) -> dict[str, FigureSource]:
    archive = Archive(root, None)  # read-only Archive operations never emit events
    result = {}
    for fid, ref in sorted(refs.items()):
        if ref.artifact_id != f"artifacts/figures/{fid}":
            raise ValueError("Figure identity mismatch")
        manifest = archive.get(ref)
        stored = archive.stored_path(ref)
        if stored is None or manifest_bytes(dir_manifest(safe_path(stored, root=root), [])) != manifest:
            raise ValueError("Figure archive bytes differ from exact ref")
        figure = Figure.model_validate_json(safe_path(stored / "figure.json", root=root).read_bytes())
        if figure.fig_id != fid:
            raise ValueError("Figure metadata identity mismatch")
        result[fid] = FigureSource(ref=ref, data=figure)
    return result


def hypotheses(bundle):
    return {
        str(h.get("id") or f"H{i}") if isinstance(h, dict) else f"H{i}": h
        for i, h in enumerate((bundle.idea.data or {}).get("hypotheses", []), 1)
    }


def planning_context(bundle: EvidenceBundle, figures: dict[str, FigureSource]) -> dict:
    return {
        "idea": bundle.idea.model_dump(mode="json"),
        "plan": bundle.plan.model_dump(mode="json"),
        "hypotheses": hypotheses(bundle),
        "aggregates": [a.model_dump(mode="json") for a in bundle.aggregates],
        "figures": {fid: f.model_dump(mode="json") for fid, f in sorted(figures.items())},
        "approved_logs": [log.model_dump(mode="json") for log in bundle.logs],
        "literature": [p.model_dump(mode="json") for p in bundle.papers],
        "runtime": bundle.runtime.model_dump(mode="json") if bundle.runtime else None,
        "allowed": {
            "aggregates": [a.aggregate_id for a in bundle.aggregates],
            "figures": sorted(figures),
            "runs": sorted({rid for a in bundle.aggregates for row in a.data.rows for rid in row.run_ids}),
            "papers": [p.paper_id for p in bundle.papers],
            "plan": bundle.plan_ref.model_dump(),
            "logs": [log.ref.model_dump() for log in bundle.logs],
            "plan_parameters": [
                {"experiment_id": e["id"], "parameter": key}
                for e in (bundle.plan.data or {}).get("experiments", [])
                for key, value in e.get("fixed", {}).items()
                if type(value) in {int, float} and math.isfinite(value)
            ],
        },
    }


def value_key(selector: dict) -> str:
    """Readable identity plus full digest; collisions are independently checked on installation."""
    readable = ".".join(str(selector[k]) for k in ("aggregate_id", "metric", "stat"))
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", readable)[:80]
    return readable + "." + sha256_bytes(encoded(selector))


def _format(value: PlannedValue, source, figures):
    if value.stat == "n":
        return {"scale": 1, "decimals": 0, "suffix": ""}
    if value.stat == "p_value":
        return {"scale": 1, "decimals": 4, "suffix": ""}
    specs = [
        f.data.spec
        for f in figures.values()
        if f.data.aggregate_ref == source.ref and f.data.spec.metric == value.metric
    ]
    scales = {spec.scale for spec in specs if spec.ylabel.strip()}
    if len(scales) != 1 or not all(math.isfinite(s) and s > 0 for s in scales):
        raise ValueError("metric presentation scale/unit must come from an exact validated Figure")
    return {"scale": scales.pop(), "decimals": 4, "suffix": ""}


def _bind_value(root, planned, sources, figures):
    source = sources.get(planned.aggregate_id)
    if source is None:
        raise ValueError("value aggregate is outside the frozen bundle")
    # Verify the archived identity, but resolve selectors against the frozen object.
    actual = runs.load_aggregate(root, source.aggregate_id, version=source.ref.version)
    if actual != source.data:
        raise ValueError("frozen Aggregate does not match its archived version")
    contrast = planned.contrast.model_dump() if planned.contrast else None
    if (planned.stat in {"diff_mean", "p_value"}) != (contrast is not None):
        raise ValueError("contrast is required only for contrast statistics")
    if contrast is not None and planned.group:
        raise ValueError("contrast statistics require empty group and exact a/b selectors")
    number = runs.resolve_value(
        root, planned.aggregate_id, planned.group, planned.metric, planned.stat, contrast, aggregate=source.data
    )
    if not math.isfinite(number):
        raise ValueError("selected statistic is unavailable/non-finite")
    selector = planned.model_dump(exclude={"name"})
    fmt = _format(planned, source, figures)
    return ValueRef(
        key=value_key(selector),
        aggregate_id=source.aggregate_id,
        aggregate_version=source.ref.version,
        group=planned.group,
        metric=planned.metric,
        stat=planned.stat,
        contrast=contrast,
        fmt=fmt,
        rendered=evidence.render_value(number, fmt),
    )


def _link(planned, bundle, figures):
    sources = {a.aggregate_id: a for a in bundle.aggregates}
    selector = dict(planned.selector or {})
    version = None
    if planned.type == "aggregate":
        source = sources.get(planned.ref)
        if source is None:
            raise ValueError("unknown aggregate evidence")
        version = source.ref.version
        if "eval_condition" in selector and selector["eval_condition"] != source.data.eval_condition:
            raise ValueError("evidence condition differs from Aggregate")
        if "group" in selector and not any(
            r.group == selector["group"] and r.metric == selector.get("metric", r.metric) for r in source.data.rows
        ):
            raise ValueError("evidence group is absent")
        if "metric" in selector and not any(r.metric == selector["metric"] for r in source.data.rows):
            raise ValueError("evidence metric is absent")
    elif planned.type == "figure":
        if planned.ref not in figures or selector:
            raise ValueError("unknown figure evidence or unsupported selector")
        figure = figures[planned.ref]
        if figure.data.aggregate_ref not in [a.ref for a in bundle.aggregates]:
            raise ValueError("figure is outside bundle Aggregate basis")
        version = figure.ref.version
    elif planned.type == "run":
        if (
            planned.ref not in {rid for a in bundle.aggregates for row in a.data.rows for rid in row.run_ids}
            or selector
        ):
            raise ValueError("run does not support selected evidence")
    elif planned.type == "paper":
        if planned.ref not in {p.paper_id for p in bundle.papers} or selector or not planned.citation_scope:
            raise ValueError("unknown paper or missing explicit citation_scope")
        selector = {"citation_scope": planned.citation_scope}
    elif planned.type == "plan":
        if planned.ref != bundle.plan_ref.artifact_id:
            raise ValueError("plan is not the exact approved plan")
        if selector:
            ids = selector["experiments"]
            experiments = {e.id for e in parse_plan_md(bundle.plan.text).experiments}
            if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                    or len(ids) != len(set(ids)) or set(ids) - experiments):
                raise ValueError("plan evidence experiments must be distinct existing approved experiment IDs")
        version = bundle.plan_ref.version
    else:
        logs = [log for log in bundle.logs if log.ref.artifact_id == planned.ref]
        if len(logs) != 1 or selector:
            raise ValueError("log is not an approved bundled log")
        version = logs[0].ref.version
    if planned.type != "paper" and planned.citation_scope:
        raise ValueError("citation_scope is only valid for paper evidence")
    return EvidenceLink(type=planned.type, ref=planned.ref, version=version, selector=selector or None)


def _bind_parameter(root, planned, bundle, links):
    plan_links = [link for link in links if link.type == "plan" and (
        not link.selector or planned.experiment_id in link.selector["experiments"]
    )]
    if not plan_links:
        raise ValueError("plan parameter requires approved plan evidence covering its experiment_id")
    plan = parse_plan_md(Archive(root, None).read_text(bundle.plan_ref))
    if plan.model_dump(mode="json") != bundle.plan.data:
        raise ValueError("frozen plan parameters do not match the exact archived plan")
    experiment = next((e for e in plan.experiments if e.id == planned.experiment_id), None)
    if experiment is None or planned.parameter not in experiment.fixed:
        raise ValueError("plan parameter must select an existing fixed key; grids/defaults cannot be inferred")
    number = experiment.fixed[planned.parameter]
    if type(number) not in {int, float} or not math.isfinite(number):
        raise ValueError("plan parameter must select a finite numeric fixed value")
    link = EvidenceLink(
        type="plan", ref=bundle.plan_ref.artifact_id, version=bundle.plan_ref.version,
        selector={"experiments": [planned.experiment_id]},
        note="Code-resolved approved plan parameter: " + json.dumps(
            {"parameter": planned.parameter, "value": number}, sort_keys=True, allow_nan=False
        ),
    )
    return str(number), link


def _numeric_text(template, bundle):
    """Ignore exact approved identifiers, while retaining every literal measurement."""
    text = re.sub(r"\\cite\{[^}]*\}", "", active_text(TOKEN.sub("", template)))
    identifiers = set(IDENTIFIER.findall(bundle.idea.text + "\n" + bundle.plan.text))
    for name in sorted(identifiers, key=len, reverse=True):
        text = re.sub(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", " ", text)
    return text


def response_plan(payload):
    """Recover a structured rejected candidate for feedback, never trust it as bound output."""
    if isinstance(payload, str):
        try:
            block = re.search(r"```(?:json)?\s*(.*?)```", payload, re.S)
            payload = json.loads(block[1] if block else payload)
        except (ValueError, TypeError):
            return None
    return payload if isinstance(payload, dict) and isinstance(payload.get("claims"), list) else None


def latex_errors(text):
    """Reject comments and broken groups before an immutable claim reaches Writer."""
    errors = []
    if any(c == "%" and not _is_escaped(text, i) for i, c in enumerate(text)):
        errors.append(r"unescaped % starts a LaTeX comment; use \% only for a validated percent unit")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text) or "^^" in text:
        errors.append("unsupported control characters/TeX character-code escapes in claim")
    depth = 0
    for i, c in enumerate(active_text(text)):
        if c in "{}" and not _is_escaped(text, i):
            depth += 1 if c == "{" else -1
            if depth < 0:
                break
    if depth:
        errors.append("unbalanced LaTeX braces in claim text_template; balance groups before binding")
    return errors


def _value_labels(value, figures):
    return sorted({f.data.spec.ylabel for f in figures.values()
                   if f.data.aggregate_ref.artifact_id == f"artifacts/aggregates/{value.aggregate_id}.json"
                   and f.data.aggregate_ref.version == value.aggregate_version
                   and f.data.spec.metric == value.metric})


def value_contract(value, name, figures):
    """Expose the actual display contract, never ask a prose model to recompute it."""
    return {"name": name, "metric": value.metric, "stat": value.stat,
            "ordered_contrast": value.contrast, "rendered": value.rendered,
            "format": value.fmt, "validated_figure_labels": _value_labels(value, figures),
            "text_template_example": f"The observed mean difference is [[{name}]]."
            if value.stat == "diff_mean" else f"The observed value is [[{name}]]."}


def presentation_errors(planned, values, figures):
    """Conservative unit/sign checks, not a general scientific-semantic review.

    A difference is signed a-b in the validated Figure's display scale. Magnitudes
    and relative percentages need separate derived evidence, which ValueRef does
    not currently supply. Ask for neutral difference prose rather than guessing.
    """
    errors = []
    units = {"seconds": r"s|sec|secs|seconds?", "milliseconds": r"ms|milliseconds?",
             "minutes": r"min|mins|minutes?"}
    for selector, value in zip(planned.values, values, strict=True):
        token = f"[[{selector.name}]]"
        figure_labels = _value_labels(value, figures)
        labels = " ".join(figure_labels)
        percent_scale = bool(figure_labels) and all(
            re.search(r"%|\bpercent(?:age)?\b", label, re.I) for label in figure_labels
        )
        for match in re.finditer(re.escape(token), planned.text_template):
            before, after = planned.text_template[:match.start()], planned.text_template[match.end():]
            left = re.split(r"[.;!?\n]", before)[-1]
            right = re.split(r"[.;!?\n]", after)[0]
            # Use the immediate suffix for units; other placeholders have their own contracts.
            unit = re.match(
                r"[\s})$]*(\\?%|percentage\s+points?|percent(?:age)?|seconds?|milliseconds?|minutes?|"
                r"secs?|ms|mins?|s)(?![A-Za-z])",
                after, re.I,
            )
            unit_name = unit[1].lower().replace("\\", "") if unit else ""
            points = unit_name.startswith("percentage point")
            percent = unit_name in {"%", "percent", "percentage"}
            prefix = TOKEN.split(left)[-1]
            points = points or bool(re.search(r"\bpercentage[-\s]+points?\b", prefix, re.I))
            prefix = re.sub(r"\bpercentage[-\s]+points?\b", "", prefix, flags=re.I)
            percent = percent or bool(re.search(r"%|\bpercent(?:age)?\b", prefix, re.I))
            if (points or percent) and (not percent_scale or value.stat in {"n", "p_value"}):
                errors.append(f"[[{selector.name}]]: percent/percentage-point wording disagrees with "
                              f"validated Figure labels {labels!r} and scale={value.fmt['scale']}; "
                              "keep the native display unit, do not append a percent unit")
            if value.stat == "diff_mean" and percent:
                errors.append(f"[[{selector.name}]]: diff_mean is an absolute signed a-b difference, "
                              "not a relative percent reduction; use neutral difference wording")
            if unit_name and not (points or percent):
                aliases = next(pattern for pattern in units.values() if re.fullmatch(pattern, unit_name))
                if (value.stat in {"n", "p_value"}
                        or not re.search(r"\b(?:" + aliases + r")\b", labels, re.I)):
                    errors.append(f"[[{selector.name}]]: unit {unit_name!r} is absent from validated "
                                  f"Figure labels {labels!r}; do not invent or convert units")
            if value.stat != "diff_mean":
                continue
            clause = left + token + right
            # Signed values must not be used as an unsigned reduction/increase magnitude.
            directional = re.search(r"\b(?:higher|lower|faster|slower|reduc\w*|decreas\w*|increas\w*|"
                                    r"improv\w*|speedup)\b", clause, re.I)
            if not re.search(r"\bdifference\b", left, re.I) or directional:
                errors.append(f"[[{selector.name}]]: state diff_mean as the signed difference "
                              f"a-b for {value.contrast}; do not write it as a higher/lower or "
                              "reduction magnitude. Use: " + value_contract(value, selector.name, figures)[
                                  "text_template_example"])
            if " minus " in clause.lower() and all(len(g) == 1 for g in value.contrast.values()):
                a, b = (str(next(iter(value.contrast[g].values()))) for g in ("a", "b"))
                plain = clause.replace(r"\_", "_")
                if not re.search(re.escape(a) + r"\s+minus\s+" + re.escape(b), plain, re.I):
                    errors.append(f"[[{selector.name}]]: written subtraction order must be {a} minus {b}")
    return errors


def correction_context(root, previous, reason, bundle, figures):
    """Give the model actionable per-slot repairs using only the current frozen evidence."""
    plan = response_plan(previous)
    feedback = {"previous": plan if plan is not None else previous, "errors": [reason], "repairs": []}
    if plan is None:
        feedback["repairs"].append({"path": "claims", "action": "Return complete ClaimPlan JSON matching the schema."})
        return feedback
    try:
        _, errors = bind_plan(root, plan, bundle, figures)
    except (AirError, ValueError, KeyError, TypeError) as exc:
        errors = [f"binding: {exc}"]
    feedback["errors"] = errors or [reason]
    sources = {a.aggregate_id: a for a in bundle.aggregates}
    experiments = (bundle.plan.data or {}).get("experiments", [])
    for i, raw in enumerate(plan["claims"], 1):
        issues = [error for error in errors if error.startswith(f"CL{i}:")]
        if not issues or not isinstance(raw, dict):
            continue
        repair = {"claim_id": f"CL{i}", "path": f"claims[{i - 1}]", "issues": issues, "actions": []}
        if any("LaTeX" in issue or "unescaped %" in issue for issue in issues):
            repair["actions"].append(
                r"Repair text_template itself: balance braces and remove raw % comments. Escape a "
                r"literal percent as \% only when the selected value actually uses a percent unit."
            )
        contracts = []
        raw_values = raw.get("values")
        for value in raw_values if isinstance(raw_values, list) else []:
            try:
                selector = PlannedValue.model_validate(value)
                bound = _bind_value(root, selector, sources, figures)
                contracts.append(value_contract(bound, selector.name, figures))
            except (AirError, ValueError, KeyError, TypeError, OSError):
                continue
        if contracts:
            repair["value_contracts"] = contracts
            repair["actions"].append(
                "Rewrite each failing measurement clause using its text_template_example. "
                "Keep [[NAME]] selectors and the validated display scale; do not copy rendered numbers "
                "into prose. diff_mean is signed a-b, never a relative percent or unsigned reduction. "
                "Separate different metrics into sentences; do not change evidence to fit the wording."
            )
        comp = raw.get("comparison") if isinstance(raw.get("comparison"), dict) else {}
        if comp:
            repair["actions"].append(
                "Bind a visible [[DIFF]] placeholder to nonempty values from comparison.aggregate_id. "
                "Keep exact ordered a/b groups and metric; split values from other Aggregates into other claims."
            )
            aid = comp.get("aggregate_id")
            source = sources.get(aid) if isinstance(aid, str) else None
            if source:
                contrast = next((c for c in source.data.contrasts if c.a == comp.get("a")
                                 and c.b == comp.get("b") and c.metric == comp.get("metric")), None)
                if contrast:
                    repair["value_example"] = {
                        "name": "DIFF", "aggregate_id": source.aggregate_id, "group": {},
                        "metric": contrast.metric, "stat": "diff_mean",
                        "contrast": {"a": contrast.a, "b": contrast.b},
                    }
                    repair["text_template_example"] = "The observed mean difference is [[DIFF]]."
        if raw.get("type") == "method_fact":
            repair["actions"].append(
                "Replace literal configuration numbers with plan_parameters placeholders. "
                "Each parameter selects an exact numeric experiment.fixed key; code supplies its value."
            )
            requested = set()
            evidence_links = raw.get("evidence")
            for link in evidence_links if isinstance(evidence_links, list) else []:
                if not isinstance(link, dict) or link.get("type") != "plan":
                    continue
                selector = link.get("selector")
                ids = selector.get("experiments", []) if isinstance(selector, dict) else []
                if isinstance(ids, list):
                    requested.update(eid for eid in ids if isinstance(eid, str))
            repair["parameter_examples"] = [
                {"name": key.upper(), "experiment_id": e["id"], "parameter": key}
                for e in experiments if not requested or e["id"] in requested
                for key, value in e.get("fixed", {}).items() if type(value) in {int, float} and math.isfinite(value)
            ]
        repair["actions"].append(
            "Evidence selector fields: aggregate=group/metric/eval_condition, plan=experiments, all others=null. "
            "Do not fix an error by removing required comparison/hypothesis coverage."
        )
        feedback["repairs"].append(repair)
    feedback["required_results"] = [
        {"aggregate_id": a.aggregate_id, "metric": c.metric, "a": c.a, "b": c.b}
        for a in bundle.aggregates for c in a.data.contrasts
    ]
    feedback["required_hypotheses"] = sorted(hypotheses(bundle))
    return feedback


def _bind_claim(root, planned, claim_id, bundle, figures):
    if planned.section == "results" and planned.statement_kind != "observation":
        raise ValueError("Results claims must be observations")
    if planned.statement_kind == "interpretation" and planned.hedge == "none":
        raise ValueError("interpretation requires likely/possible hedge")
    if planned.hypothesis and planned.hypothesis not in hypotheses(bundle):
        raise ValueError("unknown hypothesis")
    sources = {a.aggregate_id: a for a in bundle.aggregates}
    names = [v.name for v in [*planned.values, *planned.plan_parameters]]
    template = planned.text_template
    if not template.strip() or len(names) != len(set(names)) or set(TOKEN.findall(template)) != set(names):
        raise ValueError("claim placeholders must match unique planned values")
    clean = active_text(TOKEN.sub("", template))
    if re.search(r"\\(?:claim|airval)\b", clean):
        raise ValueError("model cannot supply claim IDs or airval keys")
    numeric_text = _numeric_text(template, bundle)
    if re.search(r"(?<![\w])[-+]?\d+(?:\.\d+)?", numeric_text):
        raise ValueError("literal claim numbers must be planned placeholders; use plan_parameters for approved "
                         "fixed configuration and values for measured statistics")
    values = [_bind_value(root, v, sources, figures) for v in planned.values]
    errors = latex_errors(template) + presentation_errors(planned, values, figures)
    if errors:
        raise ValueError("; ".join(errors))
    links = [_link(link, bundle, figures) for link in planned.evidence]
    if not links:
        raise ValueError("claim requires frozen evidence")
    parameters = {}
    for parameter in planned.plan_parameters:
        parameters[parameter.name], link = _bind_parameter(root, parameter, bundle, links)
        links.append(link)
    for v in values:
        if not any(e.type == "aggregate" and e.ref == v.aggregate_id for e in links):
            links.append(EvidenceLink(type="aggregate", ref=v.aggregate_id, version=v.aggregate_version))
    paper_ids = {e.ref for e in links if e.type == "paper"}
    if planned.type == "citation" and not paper_ids:
        raise ValueError("citation claim requires paper evidence")
    allowed = {
        p.record.get("citation_key"): p.paper_id
        for p in bundle.papers
        if p.status in {"verified", "metadata_only"} and p.record.get("bibtex")
    }
    for command in commands(template):
        if command.name == "cite" and any(allowed.get(k.strip()) not in paper_ids for k in command.value.split(",")):
            raise ValueError("citation is not linked to allowed frozen paper evidence")
    comparison = None
    if planned.type == "comparative" and planned.comparison is None:
        raise ValueError("comparative claim needs exact comparison")
    if planned.comparison:
        comp = planned.comparison
        source = sources.get(comp.aggregate_id)
        if source is None or not values or {v.aggregate_id for v in values} != {comp.aggregate_id}:
            raise ValueError("comparison must match the claim's bound Aggregate")
        diff = runs.resolve_value(
            root, comp.aggregate_id, {}, comp.metric, "diff_mean", {"a": comp.a, "b": comp.b}, aggregate=source.data
        )
        if (comp.asserted == "a>b" and diff <= 0) or (comp.asserted == "a<b" and diff >= 0):
            raise ValueError("planned comparison contradicts frozen evidence")
        comparison = Comparison.model_validate(comp.model_dump(exclude={"aggregate_id"}))
    replacements = {v.name: "\\airval{" + value.key + "}" for v, value in zip(planned.values, values, strict=True)}
    replacements.update(parameters)
    inner = TOKEN.sub(lambda m: replacements[m[1]], template)
    if "[[" in inner or "]]" in inner:
        raise ValueError("unknown claim placeholder syntax")
    # Bind content to its bundle even if newer evidence yields identical display values.
    links.append(
        EvidenceLink(
            type="plan",
            ref=bundle.plan_ref.artifact_id,
            version=bundle.plan_ref.version,
            note="Frozen Writing evidence SHA256: "
            + sha256_bytes(
                encoded(
                    {
                        "bundle_sha256": sha256_bytes(bundle.to_bytes()),
                        "figures": {fid: f.ref.model_dump() for fid, f in sorted(figures.items())},
                    }
                )
            ),
        )
    )
    return Claim(
        claim_id=claim_id,
        text=f"\\claim{{{claim_id}}}{{{inner}}}",
        values=values,
        evidence=links,
        comparison=comparison,
        **planned.model_dump(exclude={"text_template", "values", "plan_parameters", "evidence", "comparison"}),
    )


def coverage_errors(claims, bundle):
    errors = []
    results = [c for c in claims if c.section == "results" and c.statement_kind == "observation"]
    for hid in hypotheses(bundle):
        if not any(c.hypothesis == hid for c in results):
            errors.append(f"coverage: hypothesis {hid} has no Results observation")
    for source in bundle.aggregates:
        matching = [c for c in results if any(v.aggregate_id == source.aggregate_id for v in c.values)]
        if not matching:
            errors.append(f"coverage: comparison {source.aggregate_id} absent from Results")
        for contrast in source.data.contrasts:
            if not any(
                c.comparison
                and c.comparison.a == contrast.a
                and c.comparison.b == contrast.b
                and c.comparison.metric == contrast.metric
                for c in matching
            ):
                errors.append(f"coverage: contrast {source.aggregate_id} {contrast.a} vs {contrast.b} absent")
    return errors


def bind_plan(root: Path, plan: dict, bundle: EvidenceBundle, figures: dict[str, FigureSource]):
    """Collect per-slot errors, preserving CL<n> identities even when optional slots are dropped."""
    if bundle.diagnostics:
        raise ValueError("cannot bind claims to inconsistent evidence lineage")
    if set(plan) != {"claims"} or not isinstance(plan["claims"], list):
        raise ValueError("claim plan must contain only the claims list")
    claims, errors = [], []
    for i, raw in enumerate(plan["claims"], 1):
        try:
            claims.append(_bind_claim(root, PlannedClaim.model_validate(raw), f"CL{i}", bundle, figures))
        except (AirError, ValueError, KeyError, OSError) as exc:
            errors.append(f"CL{i}: {exc}")
    try:
        unique_values(claims)
    except ValueError as exc:
        errors.append(f"binding: {exc}")
    return claims, errors + coverage_errors(claims, bundle)


def unique_values(claims):
    values = {}
    for claim in claims:
        for value in claim.values:
            if not SAFE_KEY.fullmatch(value.key):
                raise ValueError("unsafe ValueRef key")
            if value.key in values and values[value.key] != value:
                raise ValueError(f"conflicting ValueRef key collision: {value.key}")
            values[value.key] = value
    return dict(sorted(values.items()))


def claims_bytes(claims):
    if len({c.claim_id for c in claims}) != len(claims):
        raise ValueError("duplicate claim IDs")
    unique_values(claims)
    return b"".join(encoded(c.model_dump(mode="json")) + b"\n" for c in claims)


def values_tex(claims):
    lines = [
        "% Code-generated evidence values; do not edit.",
        r"\makeatletter",
        r"\newcommand{\airval}[1]{\@ifundefined{airval@#1}{\textbf{??#1??}}{\@nameuse{airval@#1}}}",
        r"\makeatother",
    ]
    for key, value in unique_values(claims).items():
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", value.rendered):
            raise ValueError("bound numeric macro must contain only deterministic numeric text")
        lines.append(r"\expandafter\def\csname airval@" + key + r"\endcsname{" + value.rendered + "}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def insert_markers(text, claims, section):
    expected = {c.claim_id: c for c in claims if c.section == section}
    active = active_text(text)
    if re.search(r"\\(?:claim|airval)\b", active):
        raise ValueError("model must use claim markers, not claim/airval commands")
    found = MARKER.findall(active)
    if len(found) != len(set(found)) or set(found) != set(expected):
        raise ValueError("missing, duplicate, unknown or wrong-section claim marker")
    # Only active markers count; comments/literals cannot satisfy required claims.
    spans = list(MARKER.finditer(active))
    for match in reversed(spans):
        text = text[: match.start()] + expected[match[1]].text + text[match.end() :]
    if "[[CLAIM:" in active_text(text):
        raise ValueError("malformed claim marker")
    return text


def validate_bound_section(text, claims, section):
    """Exact active wrappers, keys and multiplicity; surrounding prose remains editable."""
    expected = [c for c in claims if c.section == section]
    remainder = text
    for c in expected:
        starts = [m.start() for m in re.finditer(re.escape(c.text), text)]
        starts = [i for i in starts if active_text(text)[i : i + 6] == r"\claim"]
        if len(starts) != 1:
            raise ValueError(f"bound claim {c.claim_id} missing, duplicated or altered; reconcile explicitly")
        start = starts[0]
        remainder = remainder[:start] + " " * len(c.text) + remainder[start + len(c.text) :]
    if re.search(r"\\(?:claim|airval)\b|\[\[CLAIM:", active_text(remainder)):
        raise ValueError("unknown/wrong-section claim or unbound airval key")


def locate_claims(root, claims):
    located = []
    for section in SECTIONS:
        text = safe_path(Path(root) / f"paper/sections/{section}.tex", root=root).read_text(encoding="utf-8")
        validate_bound_section(text, claims, section)
        for claim in claims:
            if claim.section == section:
                located.append(
                    claim.model_copy(
                        update={
                            "location": {
                                "file": f"paper/sections/{section}.tex",
                                "line": text[
                                    : next(
                                        m.start()
                                        for m in re.finditer(re.escape(claim.text), text)
                                        if active_text(text)[m.start() : m.start() + 6] == r"\claim"
                                    )
                                ].count("\n")
                                + 1,
                            }
                        }
                    )
                )
    by_id = {c.claim_id: c for c in located}
    return [by_id[c.claim_id] for c in claims]


def validate_preamble(text):
    active = active_text(text)
    if active.count(PREAMBLE) != 1:
        raise ValueError("bound manuscript must retain the exact code-owned evidence preamble")
    remainder = active.replace(PREAMBLE, "", 1)
    if re.search(r"\\(?:claim|airval)\b|\b(?:airval@|air_values)\b|\\csname", remainder):
        raise ValueError("manuscript redefines/overrides reserved evidence macros")
