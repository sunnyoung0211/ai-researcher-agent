"""Writing-only, context-aware repairs for literal TeX characters.

This is not a TeX interpreter or a safety filter. Only unambiguous text repairs
are automatic; Writer's command/evidence checks still run on the result.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from ._latex import TemplateError, _inactive_spans, _is_escaped, active_text

RULES = r"""LaTeX literal-character rules (apply to prose, not TeX syntax):
Write train\_seconds, R\&D, \#tag, and 10\% for literal _, &, #, and %.
Write \$ for a currency/literal dollar, \textasciicircum{} for a literal caret,
\textasciitilde{} for a literal tilde, \textbackslash{} for a literal backslash,
and \{ or \} for literal braces. Keep grouping braces balanced. JSON must encode
every LaTeX backslash as \\. Do not double-escape already escaped text.
Preserve paired $...$, $$...$$, \(...\), \[...\], mathematical subscripts/powers,
table/array alignment &, normal nonbreaking ~, verb/verbatim literals, citation
keys, labels, figure paths, [[NAME]] placeholders and [[CLAIM:CLn]] markers.
Never repair syntax by changing evidence, measurements, signs, units, selectors
or assigned claims. For unmatched math delimiters, close the intended formula
or escape a literal dollar; do not guess that scientific text is an equation.
Unknown commands are not literal backslashes: remove unsafe commands, and use
\textbackslash{} only when the source actually describes a backslash/path.
"""

_OPAQUE_ARGS = {"cite", "label", "ref", "pageref", "includegraphics", "url", "path", "href", "airval"}
_MATH_ENVS = {"math", "displaymath", "equation", "equation*", "align", "align*", "aligned",
              "gather", "gather*", "multline", "multline*", "array", "matrix", "pmatrix", "bmatrix"}
_ALIGN_ENVS = {"tabular", "tabular*", "array", "align", "align*", "aligned", "matrix", "pmatrix", "bmatrix"}
_TOKEN = re.compile(r"\[\[(?:CLAIM:CL[1-9][0-9]*|[A-Za-z][A-Za-z0-9_]*)\]\]")


@dataclass(frozen=True)
class Repair:
    text: str
    replacements: list[dict]
    errors: list[str]

    def message(self):
        changes = [f"line {e['line']}: {e['before']!r} -> {e['after']!r}" for e in self.replacements[:8]]
        return "; ".join([*self.errors, *(["Unescaped LaTeX literal characters: " + "; ".join(changes)]
                                        if changes else [])])


def _argument(text, start):
    """Find a directly braced argument, leaving optional arguments untouched."""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "[":
        # Literal command options are checked by Writer. Do not rewrite them.
        end = text.find("]", i + 1)
        if end < 0:
            return None
        i = end + 1
        while i < len(text) and text[i].isspace():
            i += 1
    if i == len(text) or text[i] != "{":
        return None
    begin, depth = i, 1
    i += 1
    while i < len(text) and depth:
        if text[i] in "{}" and not _is_escaped(text, i):
            depth += 1 if text[i] == "{" else -1
        i += 1
    return (begin, i, text[begin + 1:i - 1]) if depth == 0 else None


def inspect(text: str, *, claim=False) -> Repair:
    """Preserve TeX structure; report ambiguous syntax instead of guessing intent."""
    # Escape numeric percentages before scanning: otherwise TeX comments hide
    # later markers, syntax and other literal characters on the same line.
    working, edits, errors = text, [], []
    if "^^" in active_text(text):
        return Repair(text, [], ["TeX character-code escape syntax is unsupported; remove ^^ escapes"])
    while True:
        found = None
        for start, end, literal in _inactive_spans(working):
            if literal:
                continue
            line = max(working.rfind("\n", 0, start), working.rfind("\r", 0, start)) + 1
            prefix = working[line:start]
            if claim:
                errors.append(r"unescaped % in claim starts a comment; replace a literal % with \% "
                              "and retain the validated display unit")
                continue
            if re.search(r"(?:\d+(?:[.,]\d+)?|\.\d+)\s*\$?\s*$|\]\]$", prefix):
                found = start
                break
            if prefix.strip() and not prefix.rstrip().endswith(("{", "}", "\\")):
                errors.append(f"line {working[:start].count(chr(10)) + 1}: ambiguous inline percent; "
                              r"write \% for literal text, or move an intentional comment to its own line. "
                              "Do not hide required claim markers or closing braces in a comment")
        if found is None:
            break
        edits.append({"line": working[:found].count("\n") + 1, "before": "%", "after": r"\%"})
        working = working[:found] + r"\%" + working[found + 1:]
    inactive = {start: end for start, end, _ in _inactive_spans(working)}
    result, envs, groups = [], [], []
    math = None
    i = 0
    while i < len(working):
        if i in inactive:
            end = inactive[i]
            result.append(working[i:end])
            i = end
            continue
        marker = _TOKEN.match(working, i)
        if marker:
            result.append(marker[0])
            i = marker.end()
            continue
        char = working[i]
        in_math = math is not None or any(e in _MATH_ENVS for e in envs)
        if char == "\\":
            word = re.match(r"\\([A-Za-z]+)\*?", working[i:])
            if word:
                name, end = word[1], i + word.end()
                argument = _argument(working, end)
                if name in {"begin", "end"} and argument:
                    _, end, environment = argument
                    if name == "begin":
                        envs.append(environment)
                    elif envs and envs[-1] == environment:
                        envs.pop()
                    else:
                        errors.append(f"Unbalanced LaTeX environment {environment!r}; match begin/end")
                elif name in _OPAQUE_ARGS | {"claim"} and argument:
                    _, end, _ = argument
                result.append(working[i:end])
                i = end
                continue
            pair = working[i:i + 2]
            if pair in {r"\(", r"\["}:
                if math is not None:
                    errors.append("Nested math delimiter; preserve one matching delimiter pair")
                math = {r"\(": r"\)", r"\[": r"\]"}[pair]
            elif pair in {r"\)", r"\]"}:
                if math != pair:
                    errors.append(f"Unexpected math delimiter {pair}; match its opening delimiter")
                math = None
            result.append(pair)
            i += len(pair)
            continue
        if char == "$":
            delimiter = "$$" if working.startswith("$$", i) else "$"
            if math is None:
                math = delimiter
            elif math == delimiter:
                math = None
            else:
                errors.append("Mismatched math delimiters; use matching $/$, $$ / $$ or bracket delimiters")
            result.append(delimiter)
            i += len(delimiter)
            continue
        replacement = None
        if char == "#":
            replacement = r"\#"
        elif char == "&" and not any(e in _ALIGN_ENVS for e in envs):
            replacement = r"\&"
        elif char == "_" and not in_math:
            replacement = r"\_"
        elif char == "^" and not in_math:
            replacement = r"\textasciicircum{}"
        elif char == "{":
            groups.append(i)
        elif char == "}":
            if groups:
                groups.pop()
            else:
                errors.append(r"Unmatched closing brace; balance grouping braces or use \} for literal text")
        if replacement:
            edits.append({"line": working[:i].count("\n") + 1, "before": char, "after": replacement})
        result.append(replacement or char)
        i += 1
    if math is not None:
        errors.append(r"Unclosed math delimiter; close the intended formula, or use \$ for a literal/currency dollar")
    if groups:
        errors.append(r"Unbalanced LaTeX braces; balance grouping braces or use \{ and \} for literal braces")
    if envs:
        errors.append("Unclosed LaTeX environment; add matching end commands")
    return Repair("".join(result), edits, list(dict.fromkeys(errors)))


def normalize(text: str, *, claim=False) -> str:
    repair = inspect(text, claim=claim)
    if repair.errors:
        raise ValueError("Invalid LaTeX syntax: " + repair.message())
    return repair.text


def feedback(text: str):
    try:
        repair = inspect(text)
    except TemplateError as exc:
        return {"errors": [str(exc)], "repair_actions": [RULES]}
    return {"errors": repair.errors, "replacements": repair.replacements,
            "suggested_latex": repair.text,
            "repair_actions": [RULES, "Apply every replacement above, repair remaining syntax errors, and "
                               "return a NEW complete response. Do not replay the previous rejected fragment."]}


def bind_plan(root, plan, bundle, figures):
    """Canonicalize literal characters before the immutable evidence binding."""
    from . import claims as binding

    candidate, syntax_errors = copy.deepcopy(plan), []
    if isinstance(candidate, dict) and isinstance(candidate.get("claims"), list):
        for index, raw in enumerate(candidate["claims"], 1):
            if isinstance(raw, dict) and isinstance(raw.get("text_template"), str):
                try:
                    raw["text_template"] = normalize(raw["text_template"], claim=True)
                except (ValueError, TemplateError) as exc:
                    syntax_errors.append(f"CL{index}: {exc}")
    claims, errors = binding.bind_plan(root, candidate, bundle, figures)
    invalid = {int(re.match(r"CL(\d+):", error)[1]) for error in syntax_errors}
    return ([c for c in claims if int(c.claim_id[2:]) not in invalid],
            list(dict.fromkeys([*syntax_errors, *errors])))
