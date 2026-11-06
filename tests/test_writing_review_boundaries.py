"""Pure source-boundary checks: no gateway, model, engine or training execution."""

import copy
from types import SimpleNamespace

import pytest

from airesearcher.stages.writing import review as r


def claim(number=1):
    return SimpleNamespace(claim_id=f"CL{number}", section="abstract",
                           text=f"\\claim{{CL{number}}}{{This bound observation is preserved.}}")


def unit(text, start, end, bound=None):
    return {"claim_id": bound.claim_id if bound else None, "section": "abstract",
            "source": text[start:end], "sentence": r.inner(bound) if bound else text[start:end],
            "location": {"file": "paper/sections/abstract.tex", "line": 1, "start": start, "end": end}}


def test_inline_bound_claim_splits_unbound_sentence_ranges():
    bound = claim()
    text = "Across multiple runs, " + bound.text + " Our results suggest a benefit."
    spans = r.claim_spans(text, [bound], "abstract")
    prose = list(r.unbound_spans(text, spans))
    assert [text[a:b] for a, b in prose] == ["Across multiple runs,", "Our results suggest a benefit."]
    units = [unit(text, a, b) for a, b in prose] + [unit(text, a, b, c) for a, b, c in spans]
    r.validate_units(units, {"abstract": text}, [bound])


@pytest.mark.parametrize("prefix,suffix", [("", ""), ("Prefix, ", ""), ("", " Suffix."),
                                          ("Prefix\n", "\nSuffix."), ("Prefix, ", " Suffix.")])
def test_no_prose_span_can_cover_a_bound_range(prefix, suffix):
    bound = claim()
    text = prefix + bound.text + suffix
    spans = r.claim_spans(text, [bound], "abstract")
    for a, b in r.unbound_spans(text, spans):
        assert not any(a < end and start < b for start, end, _ in spans)
        assert r"\claim" not in text[a:b]


def test_multiple_inline_claims_are_separate_protected_ranges():
    first, second = claim(), claim(2)
    text = "Before, " + first.text + " between, " + second.text + " after."
    spans = r.claim_spans(text, [first, second], "abstract")
    prose = list(r.unbound_spans(text, spans))
    assert [text[a:b] for a, b in prose] == ["Before,", "between,", "after."]


def test_real_unit_builder_does_not_recover_claim_text_from_masked_whitespace(tmp_path, monkeypatch):
    bound = claim()
    text = "Across multiple runs, " + bound.text + " Our results suggest a benefit."
    sections = tmp_path / "sections"
    sections.mkdir()
    for name in r.binding.SECTIONS:
        (sections / f"{name}.tex").write_text(text if name == "abstract" else "\n", encoding="utf-8")
    monkeypatch.setattr(r, "_scope", lambda *args: {})
    report = SimpleNamespace(claims=[SimpleNamespace(claim_id="CL1")])
    units = r.units(tmp_path, [bound], None, report)
    assert len(units) == 3
    assert [u["claim_id"] for u in units] == [None, "CL1", None]
    assert all(r"\claim" not in u["source"] for u in units if u["claim_id"] is None)


def test_legacy_unbound_span_containing_claim_is_rejected_before_model_or_edit():
    bound = claim()
    text = "Before, " + bound.text + " after."
    legacy = unit(text, 0, len(text))
    with pytest.raises(ValueError, match="protected claim"):
        r.validate_units([legacy], {"abstract": text}, [bound])
    with pytest.raises(ValueError, match="protected claim"):
        r.revision_context(legacy, [], None)
    raw = {"sentence": text, "claim_id": None, "replacement": "", "hedge": "none"}
    with pytest.raises(ValueError, match="protected claim"):
        r.validate_revision({"sentence": text, "claim_id": None}, {}, raw, None)


def test_overlapping_revision_ranges_and_wrong_source_offsets_fail_closed():
    text = "First observation. Second observation."
    first, second = unit(text, 0, 18), unit(text, 6, len(text))
    with pytest.raises(ValueError, match="overlap"):
        r.validate_units([first, second], {"abstract": text}, [])
    first["source"] = "Different source."
    with pytest.raises(ValueError, match="exact source anchor"):
        r.validate_units([first], {"abstract": text}, [])


def test_missing_or_duplicate_claim_is_a_descriptive_validation_error():
    bound = claim()
    for text in ["The claim was removed.", bound.text + bound.text]:
        with pytest.raises(ValueError, match="missing, duplicated or altered"):
            r.claim_spans(text, [bound], "abstract")


def test_cache_reuse_requires_identical_units_and_keeps_original_records():
    bound = claim()
    text = "Before, " + bound.text + " after."
    start = text.index(bound.text)
    good = unit(text, start, start + len(bound.text), bound)
    bad = unit(text, 0, len(text))
    saved = {"response": {"sentence": good["sentence"], "claim_id": "CL1", "issues": []},
             "call_id": "existing-review-call", "status": "accepted"}
    old = {"units": [good, bad], "responses": [saved, {
        "response": {"sentence": text, "claim_id": None, "issues": []}, "call_id": "wrong-range-call"}]}
    previous = copy.deepcopy(old)
    replacement = unit(text, 0, len("Before,"))
    reused, matches = r.reusable_responses([replacement, good], old)
    assert matches == {1: 0}
    assert reused == {1: saved}
    assert old == previous
