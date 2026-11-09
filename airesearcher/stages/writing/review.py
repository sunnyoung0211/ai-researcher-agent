"""Bounded semantic review and revision policy; deterministic evidence stays in services."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Literal

from pydantic import Field

from airesearcher.core.models.claim import Issue

from . import claims as binding
from . import latex_syntax
from ._latex import _is_escaped, active_text, commands

REVIEW_PROMPT = "writing/review"
REVISE_PROMPT = "writing/revise"
SemanticCode = Literal[
    "OVERCLAIM_CAUSAL", "OVERCLAIM_SCOPE", "MISSING_LIMITATION", "NO_EVIDENCE", "MIXED_INTERPRETATION"
]
SEVERITIES = {
    "OVERCLAIM_CAUSAL": "major",
    "OVERCLAIM_SCOPE": "major",
    "MISSING_LIMITATION": "major",
    "NO_EVIDENCE": "major",
    "MIXED_INTERPRETATION": "minor",
}
# Never send hard deterministic evidence failures to a prose reviser.
HARD = {"NUM_MISMATCH", "INVALID_RUN", "STALE_EVIDENCE", "CONTRADICTED", "CITATION_NOT_IN_ARCHIVE"}


class SemanticFinding(binding.StrictModel):
    code: SemanticCode
    detail: str = Field(min_length=1, max_length=1200)
    suggestion: str = Field(min_length=1, max_length=1200)


class ReviewResponse(binding.StrictModel):
    sentence: str
    claim_id: str | None
    issues: list[SemanticFinding] = Field(max_length=5)


class RevisionResponse(binding.StrictModel):
    sentence: str
    claim_id: str | None
    replacement: str = Field(max_length=2400)
    hedge: Literal["none", "likely", "possible"]


def round_limit(value):
    if type(value) is not int or not 1 <= value <= 20:
        raise ValueError("paper.max_review_rounds must be an integer between 1 and 20")
    return value


def inner(claim):
    prefix = "\\claim{" + claim.claim_id + "}{"
    if not claim.text.startswith(prefix) or not claim.text.endswith("}"):
        raise ValueError("unsupported bound claim wrapper")
    return claim.text[len(prefix) : -1]


def _blank(text, start, end):
    return text[:start] + "".join(c if c in "\r\n" else " " for c in text[start:end]) + text[end:]


def sentence_spans(text):
    """Conservative literal sentence spans, outside commands' arguments and inactive TeX.

    Commands remain in a sentence (e.g. citations); their punctuation cannot split it.
    Structural commands and complete figure/table environments are excluded.
    """
    clean = active_text(text)
    stack = []
    for cmd in commands(text):
        if cmd.name == "begin" and cmd.value in {"figure", "table"}:
            stack.append((cmd.value, cmd.start))
        elif cmd.name == "end" and stack and cmd.value == stack[-1][0]:
            _, start = stack.pop()
            clean = _blank(clean, start, cmd.end)
        elif cmd.name in {"section", "subsection", "subsubsection", "label", "begin", "end"}:
            clean = _blank(clean, cmd.start, cmd.end)
    # Ignore punctuation inside braced commands, while preserving original source spans.
    boundaries = clean
    for cmd in commands(clean):
        boundaries = _blank(boundaries, cmd.start, cmd.end)
    start = 0
    for match in re.finditer(r"[.!?。！？](?=\s|$)|\n\s*\n|$", boundaries):
        end = match.end()
        part = clean[start:end]
        left, right = len(part) - len(part.lstrip()), len(part.rstrip())
        if right > left and re.search(r"[A-Za-z\u4e00-\u9fff]", part):
            yield start + left, start + right
        start = end


def claim_spans(text, claims, section):
    """Find exact active wrappers, with explicit diagnostics rather than next()."""
    binding.validate_bound_section(text, claims, section)
    clean = active_text(text)
    spans = []
    for claim in claims:
        if claim.section != section:
            continue
        starts = [m.start() for m in re.finditer(re.escape(claim.text), text)
                  if clean[m.start():m.start() + 6] == r"\claim"]
        if len(starts) != 1:
            raise ValueError(f"Bound claim {claim.claim_id} must occur exactly once in {section}")
        spans.append((starts[0], starts[0] + len(claim.text), claim))
    return sorted(spans, key=lambda item: item[0])


def unbound_spans(text, bound):
    """Subtract claim ranges; blanking alone is not a sentence boundary."""
    remainder = active_text(text)
    for start, end, _ in bound:
        remainder = _blank(remainder, start, end)
    for start, end in sentence_spans(remainder):
        cursor = start
        for left, right, _ in bound:
            if right <= cursor or left >= end:
                continue
            if cursor < left:
                fragment = remainder[cursor:left]
                a, b = cursor + len(fragment) - len(fragment.lstrip()), cursor + len(fragment.rstrip())
                if a < b and re.search(r"[A-Za-z\u4e00-\u9fff]", remainder[a:b]):
                    yield a, b
            cursor = max(cursor, right)
        if cursor < end:
            fragment = remainder[cursor:end]
            a, b = cursor + len(fragment) - len(fragment.lstrip()), cursor + len(fragment.rstrip())
            if a < b and re.search(r"[A-Za-z\u4e00-\u9fff]", remainder[a:b]):
                yield a, b


def require_unbound(text):
    clean = active_text(text)
    if any(not _is_escaped(clean, m.start()) for m in re.finditer(r"\\(?:claim|airval)\b", clean)):
        raise ValueError("Unbound review/revision text contains a protected claim or value; "
                         "rebuild non-overlapping review units before requesting any revision")


def validate_units(review_units, sources, claims):
    """Check offsets, identities and non-overlap before sending/editing any unit."""
    ranges = {}
    for unit in review_units:
        section = unit["section"]
        text = sources[section]
        location = unit["location"]
        a, b = location["start"], location["end"]
        if (location["file"] != f"paper/sections/{section}.tex"
                or type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text)
                or text[a:b] != unit["source"]):
            raise ValueError("Review unit does not match its exact source anchor")
        bound = claim_spans(text, claims, section)
        if unit["claim_id"] is None:
            require_unbound(unit["source"])
            if any(a < end and start < b for start, end, _ in bound):
                raise ValueError("Unbound review unit overlaps a protected claim; rebuild review boundaries")
        elif not any(a == start and b == end and claim.claim_id == unit["claim_id"]
                     and unit["sentence"] == inner(claim) for start, end, claim in bound):
            raise ValueError("Bound review unit does not match its exact claim wrapper")
        ranges.setdefault(section, []).append((a, b))
    for section, spans in ranges.items():
        spans.sort()
        if any(right > next_left for (_, right), (next_left, _) in zip(spans, spans[1:], strict=False)):
            raise ValueError(f"Review/revision ranges overlap in {section}; rebuild review boundaries")


def reusable_responses(new_units, old):
    """Reuse only identical contexts; never apply an answer to a shortened span."""
    result, matches = {}, {}
    for index, unit in enumerate(new_units):
        positions = [i for i, previous in enumerate(old["units"]) if previous == unit]
        if len(positions) != 1:
            continue
        previous_index = positions[0]
        matches[index] = previous_index
        if previous_index >= len(old.get("responses", [])):
            continue
        saved = old["responses"][previous_index]
        if saved.get("rejected"):
            continue
        try:
            if not saved.get("skipped"):
                normalize(unit, saved["response"])
        except (ValueError, KeyError):
            continue
        result[index] = saved
    return result, matches


def _scope(claim, bundle, checked):
    values = claim.values
    selected = []
    for source in bundle.aggregates:
        refs = [v for v in values if v.aggregate_id == source.aggregate_id]
        links = [e for e in claim.evidence if e.type == "aggregate" and e.ref == source.aggregate_id]
        if not refs and not links:
            continue
        rows = [
            r
            for r in source.data.rows
            if any(
                r.metric == v.metric and (r.group == v.group or (v.contrast and r.group in v.contrast.values()))
                for v in refs
            )
        ]
        selected.append(
            {
                "ref": source.ref.model_dump(),
                "eval_condition": source.data.eval_condition,
                "rows": [{"group": r.group, "metric": r.metric, "n": r.n} for r in rows],
            }
        )
    papers = [
        {"paper_id": p.paper_id, "access_level": p.record.get("access_level")}
        for p in bundle.papers
        if any(e.type == "paper" and e.ref == p.paper_id for e in claim.evidence)
    ]
    return {
        "statement_kind": claim.statement_kind,
        "hedge": claim.hedge,
        "importance": claim.importance,
        "hypothesis": {"id": claim.hypothesis, "approved": binding.hypotheses(bundle).get(claim.hypothesis)},
        "task_scope": {k: bundle.runtime.task.get(k) for k in ("name", "domain", "description", "data", "models")}
        if bundle.runtime
        else None,
        "comparison": claim.comparison.model_dump() if claim.comparison else None,
        "values": [v.model_dump() for v in values],
        "recomputed": checked.recomputed,
        "aggregates": selected,
        "papers": papers,
        "evidence": [e.model_dump(exclude={"note"}) for e in claim.evidence],
    }


def units(source, claims, bundle, report):
    """Build exact anchors from the archived manuscript, never the mutable live paper."""
    result = []
    sources = {}
    checks = {c.claim_id: c for c in report.claims}
    for section in binding.SECTIONS:
        text = (source / f"sections/{section}.tex").read_bytes().decode("utf-8")
        sources[section] = text
        bound = claim_spans(text, claims, section)
        prose = list(unbound_spans(text, bound))
        targets = [(a, b, c) for a, b, c in bound]
        if section in {"abstract", "results", "conclusion"}:
            targets += [(a, b, None) for a, b in prose]
        for start, end, claim in sorted(targets, key=lambda t: t[0]):
            before = [(a, b) for a, b in prose if b <= start]
            after = [(a, b) for a, b in prose if a >= end]
            nearby = [text[a:b][:800] for a, b in (before[-1:] + after[:1])]
            sentence = inner(claim) if claim else text[start:end]
            if len(sentence) > 2400:
                raise ValueError("semantic review sentence exceeds bounded 2400-character limit; split explicitly")
            unit = {
                "claim_id": claim.claim_id if claim else None,
                "sentence": sentence,
                "section": section,
                "nearby": nearby,
                "scope": _scope(claim, bundle, checks[claim.claim_id]) if claim else {},
                "location": {
                    "file": f"paper/sections/{section}.tex",
                    "line": text[:start].count("\n") + 1,
                    "start": start,
                    "end": end,
                },
                "source": text[start:end],
            }
            if len(binding.encoded(unit)) > 24000:
                raise ValueError("semantic review unit exceeds bounded evidence context")
            result.append(unit)
    validate_units(result, sources, claims)
    return result


def model_context(unit):
    return {k: v for k, v in unit.items() if k not in {"location", "source"}}


def normalize(unit, raw):
    if unit["claim_id"] is None:
        require_unbound(unit.get("source", unit["sentence"]))
    response = ReviewResponse.model_validate(raw)
    if response.sentence != unit["sentence"] or response.claim_id != unit["claim_id"]:
        raise ValueError("untrusted semantic response: exact sentence/claim identity mismatch")
    issues = []
    for finding in response.issues:
        if (finding.code == "NO_EVIDENCE") != (unit["claim_id"] is None):
            raise ValueError("semantic code does not apply to this bound/unbound review unit")
        if finding.code == "MIXED_INTERPRETATION" and unit["section"] != "results":
            raise ValueError("MIXED_INTERPRETATION only applies to Results")
        issues.append(
            Issue(
                code=finding.code,
                severity=SEVERITIES[finding.code],
                claim_id=unit["claim_id"],
                sentence=unit["sentence"],
                location=unit["location"],
                detail=finding.detail,
                suggestion=finding.suggestion,
            )
        )
    return issues


def merge(deterministic, semantic):
    report = deterministic.model_copy(deep=True)
    for issue in semantic:
        if issue.claim_id is None:
            report.unbound_issues.append(issue)
        else:
            check = next(c for c in report.claims if c.claim_id == issue.claim_id)
            check.issues.append(issue)
    for check in report.claims:
        if any(i.code == "CONTRADICTED" for i in check.issues):
            check.support = "contradicted"
        elif check.support != "unverifiable":
            check.support = (
                "unsupported"
                if any(i.severity == "blocker" for i in check.issues)
                else "partially_supported"
                if any(i.severity == "major" for i in check.issues)
                else "supported"
            )
    counts = {"blocker": 0, "major": 0, "minor": 0}
    for issue in all_issues(report):
        counts[issue.severity] += 1
    counts.update(Counter(c.support for c in report.claims))
    report.counts = counts
    return report


def all_issues(report):
    return [i for c in report.claims for i in c.issues] + report.unbound_issues


def revision_targets(report, review_units):
    # A hard evidence failure anywhere disables automatic prose correction for this round.
    if any(i.code in HARD for i in all_issues(report)):
        return []
    return [
        (
            u,
            [
                i
                for i in all_issues(report)
                if i.code in SEVERITIES
                and i.severity == "major"
                and i.claim_id == u["claim_id"]
                and i.sentence == u["sentence"]
                and i.location == u["location"]
            ],
        )
        for u in review_units
        if any(
            i.code in SEVERITIES
            and i.severity == "major"
            and i.claim_id == u["claim_id"]
            and i.sentence == u["sentence"]
            and i.location == u["location"]
            for i in all_issues(report)
        )
    ]


# A deliberately finite wording surface, not relation extraction. Unrecognized
# prose stays entirely immutable: no heuristic can prove it is only qualification.
COMPARISON_QUALIFIERS = (
    "This observation is limited to the evaluated scope.",
    "This observation is limited to the evaluated setting.",
    "Further evaluation is needed to assess generalization.",
    "These observations do not establish causality.",
)
COMPARISON_SHELLS = (
    "This establishes universal superiority.",
    "These results generalize to all settings.",
    "This establishes a causal mechanism.",
    *COMPARISON_QUALIFIERS,
)


def _comparison_surface(sentence):
    for suffix in COMPARISON_SHELLS:
        if sentence.endswith(suffix):
            core = sentence[: -len(suffix)]
            # Only a standalone terminal shell after complete prose; preserve all
            # preceding bytes, including every factual sentence and its whitespace.
            if re.search(r"[.!?]\s+$", core):
                return core, suffix
    return sentence, ""


def comparison_revisable(unit, claim):
    return (
        not claim
        or not (claim.comparison or claim.type == "comparative")
        or bool(_comparison_surface(unit["sentence"])[1])
    )


def revision_context(unit, issues, claim):
    if claim is None:
        require_unbound(unit.get("source", unit["sentence"]))
    context = model_context(unit)
    sentence = unit["sentence"]
    placeholders = {}
    if claim:
        for index, value in enumerate(claim.values, 1):
            macro = r"\airval{" + value.key + "}"
            placeholder = f"[[VALUE_{index}]]"
            placeholders[placeholder] = macro
            sentence = sentence.replace(macro, placeholder)
        # Preserve existing citation/reference provenance without letting the model
        # author new citation keys or resource identifiers.
        citations = [c for c in commands(sentence) if c.name in {"cite", "ref", "pageref"}]
        for index, command in reversed(list(enumerate(citations, 1))):
            placeholder = f"[[CITATION_{index}]]"
            placeholders[placeholder] = sentence[command.start : command.end]
            sentence = sentence[: command.start] + placeholder + sentence[command.end :]
    # The reviser receives placeholders, never trusted macro identifiers.
    context["sentence"] = sentence
    if claim and (claim.comparison or claim.type == "comparative"):
        core, shell = _comparison_surface(sentence)
        context.update(immutable_core=core, editable_suffix=shell, allowed_suffixes=list(COMPARISON_QUALIFIERS))
    context["scope"] = {k: v for k, v in context["scope"].items() if k not in {"values", "recomputed"}}
    context["allowed_placeholders"] = list(placeholders)
    context["preserved_literal_escapes"] = dict(revision_literal_escapes(sentence))
    context["issues"] = [{"code": i.code, "detail": i.detail, "suggestion": i.suggestion} for i in issues]
    return context, placeholders


REVISION_TOKEN = r"\[\[(?:VALUE|CITATION)_\d+\]\]"
LITERAL_ESCAPE = re.compile(r"\\(?:[_&#%${}]|text(?:backslash|asciitilde|asciicircum)\{\})")
QUANTITY_WORD = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\b", re.I,
)


def revision_identifiers(sentence):
    """Only exact numeric names already in the code-owned original are exempt."""
    sentence = re.sub(REVISION_TOKEN, "", sentence)
    names = binding.IDENTIFIER.findall(sentence)
    names += re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:(?:[-_]|\\_)[A-Za-z0-9]+)+\b", sentence)
    return sorted({name for name in names if re.search(r"\d", name)})


def _identifier_pattern(name):
    return r"(?<![\w-])" + re.escape(name) + r"(?![\w-])"


def revision_literal_escapes(text):
    """Only existing active literal glyph escapes may survive restricted revision."""
    clean = active_text(text)
    return Counter(m[0] for m in LITERAL_ESCAPE.finditer(clean) if not _is_escaped(clean, m.start()))


def _revision_surface(text, original):
    text, original = (re.sub(REVISION_TOKEN, "", value) for value in (text, original))
    allowed = revision_literal_escapes(original)
    actual = revision_literal_escapes(text)
    for token, count in allowed.items():
        if actual[token] != count:
            raise ValueError(f"revision must preserve existing literal escape {token!r} exactly {count} time(s); "
                             "copy it unchanged instead of removing, duplicating or double-escaping it")
    for name in revision_identifiers(original):
        pattern = _identifier_pattern(name)
        if len(re.findall(pattern, text)) != len(re.findall(pattern, original)):
            raise ValueError(f"revision must preserve the exact existing identifier {name!r} and its multiplicity")
        text = re.sub(pattern, " ", text)
    # Mask only glyph escapes inherited from the exact source, not arbitrary TeX
    # commands or additional occurrences of an otherwise allowed escape.
    clean = active_text(text)
    text = LITERAL_ESCAPE.sub(
        lambda m: " " * len(m[0]) if m[0] in allowed and not _is_escaped(clean, m.start()) else m[0], text
    )
    return text


def validate_revision(context, placeholders, raw, claim):
    if claim is None:
        require_unbound(context["sentence"])
    response = RevisionResponse.model_validate(raw)
    if response.sentence != context["sentence"] or response.claim_id != context["claim_id"]:
        raise ValueError("revision exact sentence/claim identity mismatch")
    text = response.replacement
    token = REVISION_TOKEN
    expected = Counter(re.findall(token, context["sentence"]))
    actual = Counter(re.findall(token, text))
    if actual != expected:
        raise ValueError("revision must preserve exact value placeholder multiplicities")
    surface = _revision_surface(text, context["sentence"]) if text.strip() or claim else ""
    if "[[" in surface or "]]" in surface:
        raise ValueError("unknown revision placeholder")
    # Restrict revision to literal wording + existing value slots. Citations, figures,
    # TeX commands, comments and numbers cannot be manufactured or concealed.
    forbidden = re.findall(r"[\\{}%$#&^~]|(?<![\w])[-+]?\d+(?:\.\d+)?", surface)
    if forbidden:
        raise ValueError("revision may not add commands, citations, figures or raw numbers; offending tokens: "
                         + repr(forbidden))
    original = _revision_surface(context["sentence"], context["sentence"])
    new_quantities = Counter(QUANTITY_WORD.findall(surface.lower())) - Counter(QUANTITY_WORD.findall(original.lower()))
    if new_quantities:
        raise ValueError("revision may not add spelled-out numeric quantities: " + ", ".join(new_quantities)
                         + "; use a qualitative limitation such as 'a limited number of runs' instead")
    if claim:
        if not text.strip():
            raise ValueError("bound Claim cannot be removed")
        if claim.comparison or claim.type == "comparative":
            # Recompute the boundary from the exact original, never from model fields.
            core, shell = _comparison_surface(context["sentence"])
            if text != context["sentence"] and (
                not shell or not text.startswith(core) or text[len(core) :] not in COMPARISON_QUALIFIERS
            ):
                raise ValueError("revision changed immutable comparison facts or unsupported semantic shell")
        # Retain the existing lexical defense for non-comparative Claims. Comparative
        # facts are protected by exact bytes above, not by counts of direction words.
        direction = (
            r"\b(?:higher|lower|greater|less|larger|smaller|positive|negative|"
            r"increased?|decreased?|not|non-significant)\b"
        )
        if not (claim.comparison or claim.type == "comparative") and Counter(
            re.findall(direction, context["sentence"].lower())
        ) != Counter(re.findall(direction, text.lower())):
            raise ValueError("revision changed comparison direction or negative-result wording")
        if {"none": 0, "likely": 1, "possible": 2}[response.hedge] < {"none": 0, "likely": 1, "possible": 2}[
            claim.hedge
        ]:
            raise ValueError("revision cannot strengthen hedge")
        for placeholder, macro in placeholders.items():
            text = text.replace(placeholder, macro)
        text = latex_syntax.normalize(text)
        revised = claim.model_copy(update={"text": f"\\claim{{{claim.claim_id}}}{{{text}}}", "hedge": response.hedge})
        return revised.text, revised
    if response.hedge != "none":
        raise ValueError("unbound prose has no Claim hedge field")
    return latex_syntax.normalize(text), None


def correction_context(context, previous, attempt_number, *, claim=None, placeholders=None, reviewing=False):
    """A new request includes the rejected candidate, concrete edits and checked examples."""
    candidate = previous.get("response", previous.get("raw"))
    if isinstance(candidate, str):
        try:
            block = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.S)
            candidate = json.loads(block[1] if block else candidate)
        except ValueError:
            pass
    errors = [previous.get("reason", "Unrecorded/interrupted response; return complete schema JSON")]
    actions = [
        "Return NEW complete schema JSON. Echo the exact supplied sentence and claim_id, not an edited target.",
        "Use the rejected candidate below as correction input; repair every reported field before returning it.",
    ]
    feedback = {"attempt_number": attempt_number, "previous": candidate, "errors": errors,
                "required_identity": {k: context[k] for k in ("sentence", "claim_id")}, "repair_actions": actions}
    if reviewing:
        actions.extend([
            "Return only sentence, claim_id and issues. Each issue has only code, detail and suggestion; "
            "severity/support/provenance are assigned by code.",
            "NO_EVIDENCE applies only to unbound prose. MIXED_INTERPRETATION applies only to Results. "
            "Reassess the supplied evidence; never erase a valid finding merely to pass validation.",
        ])
        feedback["allowed_issue_codes"] = [code for code in SEVERITIES
                                           if (code == "NO_EVIDENCE") == (context["claim_id"] is None)
                                           and (code != "MIXED_INTERPRETATION" or context["section"] == "results")]
        return feedback
    try:
        validate_revision(context, placeholders, candidate, claim)
    except (ValueError, TypeError) as exc:
        if str(exc) not in errors:
            errors.append(str(exc))
    feedback["required_placeholder_counts"] = dict(Counter(re.findall(REVISION_TOKEN, context["sentence"])))
    feedback["preserved_identifiers"] = revision_identifiers(context["sentence"])
    feedback["preserved_literal_escapes"] = dict(revision_literal_escapes(context["sentence"]))
    actions.extend([
        "Set replacement to revised prose. Preserve every original VALUE/CITATION placeholder with its exact count; "
        "do not rename, remove, duplicate or manufacture slots.",
        "Keep the exact existing dataset/model identifiers listed in preserved_identifiers. Their digits are names, "
        "not new measurements. Do not remove SST-2 merely to avoid a numeric error. "
        "For unbound NO_EVIDENCE prose, removing the entire unsupported sentence is allowed.",
        "Remove added numbers and spelled-out quantities (e.g. three runs). For a sample-size limitation use "
        "'a limited number of runs'; do not invent a new count or turn it into words to bypass binding.",
        "Remove added TeX commands/comments, citation keys and resource names. Use existing CITATION slots only. "
        "Keep comparison direction, negative findings, immutable_core and the original or weaker hedge.",
        r"Copy every preserved_literal_escapes entry exactly with its original count, e.g. keep train\_seconds "
        r"and a source \% unchanged. These are existing literal glyphs, not new commands. Do not change \_ "
        r"to _ or \\_, and do not add other commands or extra escape occurrences.",
        "A revision is restricted prose, not a new LaTeX section. Write 'and' instead of introducing &, "
        "'percent' only for an already validated percent unit instead of %, and preserve original escaped "
        "identifiers/placeholder slots. Do not introduce dollar delimiters, literal braces or new commands. "
        "Literal underscores in permitted prose are escaped by the local syntax layer after evidence checks.",
    ])
    hedge = claim.hedge if claim else "none"
    examples = []
    if "immutable_core" in context:
        examples = [context["immutable_core"] + suffix for suffix in context["allowed_suffixes"]]
    elif claim:
        suffix = (" These observations are based on a limited number of runs."
                  if any(i["code"] == "MISSING_LIMITATION" for i in context["issues"])
                  else " This observation is limited to the evaluated setting.")
        examples = [context["sentence"] + suffix]
    else:
        examples = [""]
    feedback["validated_response_examples"] = []
    for replacement in examples:
        example = {**feedback["required_identity"], "replacement": replacement, "hedge": hedge}
        try:
            validate_revision(context, placeholders, example, claim)
        except ValueError:
            continue
        feedback["validated_response_examples"].append(example)
    feedback["example_scope"] = "Examples pass structural rules; semantic adequacy still requires the next review."
    return feedback


def markdown(report, metadata):
    lines = [
        f"# Combined evidence review v{metadata['number']}",
        "",
        f"Manuscript: {report.paper_ref.model_dump()}",
        f"Claims: {metadata['claims_ref']}",
        f"Review round: {metadata['round']}",
        f"Automatic revision rounds completed: {metadata['revision_rounds']}",
        f"Completed revision receipt identities: {metadata['revision_receipts']}",
        "Deterministic check: completed; semantic review: completed (exact sentence validated)",
        f"Compile success: {report.compile_ok}",
        f"Counts: {report.counts}",
        f"Review prompt: {metadata['route']['prompt']}",
        f"Claim-review skill: {metadata['route']['skill_ref']}",
        "",
    ]
    for c in report.claims:
        lines.append(f"- {c.claim_id}: {c.support}")
        lines.extend(f"  - {i.severity} {i.code}: {i.sentence} — {i.detail}" for i in c.issues)
    lines += [f"- Unbound {i.severity} {i.code} {i.location}: {i.sentence} — {i.detail}" for i in report.unbound_issues]
    return "\n".join(lines) + "\n"
