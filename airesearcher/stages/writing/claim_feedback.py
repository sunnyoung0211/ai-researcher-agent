"""Actionable retry examples; these never replace model output or relax binding."""

from __future__ import annotations

import copy
import re

from airesearcher.core.errors import AirError

from . import claims as binding
from . import latex_syntax


def correction_context(root, previous, reason, bundle, figures):
    feedback = binding.correction_context(root, previous, reason, bundle, figures)
    plan = binding.response_plan(previous)
    if plan is None:
        return feedback
    example = copy.deepcopy(plan)
    changed = False
    _, syntax_errors = latex_syntax.bind_plan(root, plan, bundle, figures)
    feedback["errors"] = list(dict.fromkeys([*feedback["errors"], *syntax_errors]))
    for index, raw in enumerate(plan["claims"], 1):
        if not isinstance(raw, dict) or not isinstance(raw.get("text_template"), str):
            continue
        syntax = latex_syntax.feedback(raw["text_template"])
        if not syntax.get("replacements") and not syntax["errors"]:
            continue
        slot = next((r for r in feedback["repairs"] if r.get("claim_id") == f"CL{index}"), None)
        if slot is None:
            slot = {"claim_id": f"CL{index}", "path": f"claims[{index - 1}]", "issues": [], "actions": []}
            feedback["repairs"].append(slot)
        slot["latex_correction"] = syntax
        slot["actions"].extend(syntax["repair_actions"])
        slot["actions"].append("Apply replacements to text_template itself. Preserve every [[NAME]] slot, "
                               "selector, unit and ordered claim position. Return the complete new ClaimPlan.")
        if "suggested_latex" in syntax:
            candidate = {**raw, "text_template": syntax["suggested_latex"]}
            slot["replacement_claim_example"] = candidate
            example["claims"][index - 1] = candidate
            changed = True
    for repair in feedback["repairs"]:
        if not any("literal claim numbers" in issue for issue in repair["issues"]):
            continue
        index = int(repair["claim_id"][2:]) - 1
        raw = plan["claims"][index]
        repair["literal_numbers"] = re.findall(
            r"(?<![\w])[-+]?\d+(?:\.\d+)?", binding._numeric_text(raw["text_template"], bundle)
        )
        repair["actions"].append(
            "Remove EVERY unbound numeric literal listed in literal_numbers, including significance "
            "cutoffs such as p<0.05, not just the measured result. For a Results claim, report the "
            "actual p-value through its existing [[NAME]] value selector and omit the literal cutoff. "
            "Do not replace a cutoff with a fabricated measured p-value, a spelled-out number or a "
            "Results plan_parameters entry. Retain all existing value selectors and claim slots."
        )
        # A partial example with only [[DIFF]] would violate the placeholder rule
        # when the original candidate also selected a p-value or sample count.
        contracts = repair.get("value_contracts", [])
        if (not contracts or raw.get("plan_parameters")
                or len(contracts) != len(raw.get("values", []))):
            continue
        clauses = []
        for contract in contracts:
            name, stat = contract["name"], contract["stat"]
            metric = contract["metric"].replace("_", r"\_")
            if stat == "p_value":
                clauses.append(f"The corresponding p-value is [[{name}]].")
            elif stat == "n":
                clauses.append(f"The selected sample count is [[{name}]].")
            elif stat == "diff_mean":
                clauses.append(f"The observed mean difference in {metric} is [[{name}]].")
            else:
                clauses.append(contract["text_template_example"])
        candidate = copy.deepcopy(raw)
        candidate["text_template"] = " ".join(clauses)
        try:
            binding._bind_claim(root, binding.PlannedClaim.model_validate(candidate), repair["claim_id"],
                                bundle, figures)
        except (AirError, ValueError, KeyError, TypeError, OSError):
            continue
        repair["replacement_claim_example"] = candidate
        repair["text_template_example"] = candidate["text_template"]
        repair["example_validation"] = "Passed the current per-claim evidence and presentation checks."
        repair["actions"].append(
            "Use replacement_claim_example to rewrite this complete claim. It preserves all selectors, "
            "placeholders and evidence. Return the complete corrected ClaimPlan for normal validation; "
            "examples are instructions, never automatically installed output."
        )
        example["claims"][index] = candidate
        changed = True
    if changed:
        _, errors = latex_syntax.bind_plan(root, example, bundle, figures)
        feedback["remaining_errors_after_examples"] = errors
        if not errors:
            feedback["validated_response_example"] = example
    return feedback
