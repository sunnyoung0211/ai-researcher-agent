"""Durable bounded semantic attempts; explicit human decisions authorize more work."""
from pydantic import BaseModel

from airesearcher.core.models.question import Question, QuestionOption
from airesearcher.engine.stage import Continue, Stop
from airesearcher.llm.gateway import LLMOutputError, TransientLLMError


class DecisionRequired(Exception):
    def __init__(self, result):
        self.result = result


def _question_text(name, records):
    text = f"Semantic unit {name} exhausted model responses or was interrupted. Inspect attempts before proceeding."
    if records and records[-1]["status"] == "call_failed":
        failure = records[-1]
        text = f"Semantic unit {name} failed during the model call; no usable response was recorded."
        text += f" Original error: {failure['reason']}."
        if "location is not supported" in failure["reason"].lower():
            text += (" The provider rejected this location. Use a model service available to your region "
                     "and account before retrying; rewriting the manuscript cannot resolve provider access.")
        text += " Retrying may incur additional charges; no successful response or zero-cost outcome is assumed."
    return text


def complete(ctx, name, load, save, call, validate, *, allow_skip=True):
    journal = load(ctx, name) or {"attempts": [], "window_start": 0, "answers": []}
    records = journal["attempts"]

    def persist():
        save(ctx, name, journal)
        path = f"paper/.writing/{name}.json"
        return ctx.archive.put(path, "other", ctx.root / path, producer="agent:writing/attempts")

    if journal.get("skipped"):
        return None
    if records and records[-1]["status"] == "accepted":
        validate(records[-1]["response"])
        return records[-1]
    if records and records[-1]["status"] == "returned":
        try:
            validate(records[-1]["response"])
        except ValueError as exc:
            records[-1].update(status="rejected", reason=str(exc))
        else:
            records[-1]["status"] = "accepted"
            persist()
            return records[-1]
        persist()
    if records and records[-1]["status"] == "started":
        records[-1].update(status="interrupted", reason="Response not recorded; cost uncertain")
        persist()
    limit = 1 if journal["window_start"] else 2
    interrupted = records and records[-1]["status"] == "interrupted"
    call_failed = records and records[-1]["status"] == "call_failed"
    if interrupted or call_failed or len(records) - journal["window_start"] >= limit:
        text = _question_text(name, records)
        answer = ctx.answer
        if answer and answer.question_id not in journal["answers"]:
            record = ctx.questions.record(answer.question_id)
            if (record.question.text != text or record.question.stage != "writing" or record.answer != answer
                    or ctx.questions.records()[-1].question.question_id != answer.question_id
                    or answer.answered_by != "user"):
                raise ValueError("Stale semantic recovery answer")
            journal["answers"].append(answer.question_id)
            journal.setdefault("decisions", []).append(answer.model_dump(mode="json"))
            if answer.choice == "skip" and allow_skip:
                journal["skipped"] = "Model responses exhausted; user skipped; human approval required"
                persist()
                return None
            if answer.choice != "retry":
                raise ValueError("Unexpected semantic recovery answer")
            journal["window_start"] = len(records)
            persist()
        else:
            ref = persist()
            raise DecisionRequired(Stop(text, Question(text=text, refs=[ref], options=[
                QuestionOption(id="retry", label="Retry once (may incur additional model charges)"),
                *([QuestionOption(id="skip", label="Skip unit; semantic review INCOMPLETE; require human approval")]
                  if allow_skip else []),
                QuestionOption(id="end", label="End project", default=True),
            ])))
    attempt = {"number": len(records) + 1, "status": "started"}
    records.append(attempt)
    persist()  # An ambiguous call never becomes a free restart retry.
    try:
        response = call()
    except LLMOutputError as exc:
        attempt.update(status="rejected", reason=str(exc), raw=getattr(exc, "response_text", None),
                       call_id=getattr(exc, "call_id", None))
        persist()
        raise DecisionRequired(Continue("Rejected semantic response retained; bounded retry pending")) from exc
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if not isinstance(exc, TransientLLMError) and not isinstance(status, int):
            raise  # Process/storage/permission failures retain their existing recovery semantics.
        attempt.update(status="call_failed", error_type=type(exc).__name__, http_status=status,
                       reason=f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "") + f": {exc}",
                       cost_status="unknown; no successful response recorded")
        persist()
        raise DecisionRequired(Continue("Model call failure retained; explicit recovery required")) from exc
    payload = response.parsed.model_dump(mode="json") if isinstance(response.parsed, BaseModel) else response.parsed
    attempt.update(response=payload, raw=response.text, call_id=response.call_id, status="returned",
                   prompt_id=getattr(response, "prompt_id", None),
                   prompt_version=getattr(response, "prompt_version", None))
    persist()  # A completed call is durable before validating or installing output.
    try:
        validate(attempt["response"])
    except ValueError as exc:
        attempt.update(status="rejected", reason=str(exc))
        persist()
        raise DecisionRequired(Continue("Rejected semantic response retained; bounded retry pending")) from exc
    attempt["status"] = "accepted"
    persist()
    return attempt
