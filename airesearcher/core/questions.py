"""待回答问题（详细设计 1 第 3.7 节）。同一项目同一时刻最多一个待回答问题。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .errors import NotFound, StaleQuestion, ValidationFailed
from .events import Events
from .fsutil import atomic_write_json, now, read_json
from .models.question import Answer, Question, QuestionRecord


class Questions:
    def __init__(self, root: Path, events: Events):
        self.dir = Path(root) / "questions"
        self.events = events
        self._lock = threading.RLock()
        self.on_change: list[Callable[[QuestionRecord], None]] = []

    def _path(self, qid: str) -> Path:
        return self.dir / f"{qid}.json"

    def record(self, question_id: str) -> QuestionRecord:
        p = self._path(question_id)
        if not p.exists():
            raise NotFound(f"找不到问题 {question_id}")
        return QuestionRecord.model_validate(read_json(p))

    def get(self, question_id: str) -> Question:
        return self.record(question_id).question

    def records(self) -> list[QuestionRecord]:
        if not self.dir.exists():
            return []
        return [QuestionRecord.model_validate(read_json(p)) for p in sorted(self.dir.glob("q-*.json"))]

    def pending(self) -> Question | None:
        for r in reversed(self.records()):
            if r.question.status == "pending":
                return r.question
        return None

    def _next_id(self) -> str:
        nums = [int(p.stem.split("-")[1]) for p in self.dir.glob("q-*.json")] if self.dir.exists() else []
        return f"q-{max(nums, default=0) + 1:04d}"

    def _save(self, rec: QuestionRecord) -> None:
        atomic_write_json(self._path(rec.question.question_id), rec)
        for fn in list(self.on_change):
            try:
                fn(rec)
            except Exception:
                pass

    def ask(self, question: Question, stage: str) -> str:
        """由引擎在阶段返回 Stop(question=...) 时调用。已有待回答问题的，旧问题改为 withdrawn。"""
        with self._lock:
            old = self.pending()
            if old is not None:
                self.withdraw(old.question_id, "有新的问题")
            q = question.model_copy(deep=True)
            q.question_id = self._next_id()
            q.stage = stage
            q.status = "pending"
            q.created_at = now()
            self._save(QuestionRecord(question=q))
        self.events.append(
            "question.asked", f"[{stage}] {q.text.splitlines()[0][:80]}", actor=f"agent:{stage}",
            refs=q.refs, data={"question_id": q.question_id, "options": [o.id for o in q.options]},
        )
        return q.question_id

    def withdraw(self, question_id: str, reason: str = "") -> None:
        with self._lock:
            rec = self.record(question_id)
            if rec.question.status != "pending":
                return
            rec.question.status = "withdrawn"
            self._save(rec)
        self.events.append(
            "question.withdrawn", f"问题 {question_id} 已撤回" + (f"：{reason}" if reason else ""),
            actor="system", data={"question_id": question_id},
        )

    def answer(
        self, question_id: str, choice: str | None, text: str = "", request_id: str = "", by: str = "user"
    ) -> Answer:
        with self._lock:
            rec = self.record(question_id)
            if rec.answer and request_id and request_id in rec.answer.request_ids:
                return rec.answer  # 重复请求：原样返回
            current = self.pending()
            if rec.question.status != "pending" or current is None or current.question_id != question_id:
                raise StaleQuestion(f"问题 {question_id} 已不是当前待回答的问题", {"status": rec.question.status})
            q = rec.question
            if q.options:
                if choice is None:
                    raise ValidationFailed("请选择一个选项", {"options": [o.id for o in q.options]})
                if choice not in {o.id for o in q.options}:
                    raise ValidationFailed(
                        f"选项 {choice} 不存在", {"options": [o.id for o in q.options]}
                    )
            elif not text.strip():
                raise ValidationFailed("这个问题需要文字回答")
            if q.allow_text and not q.options and not text.strip():
                raise ValidationFailed("这个问题需要文字回答")
            ans = Answer(
                question_id=question_id, choice=choice, text=text, answered_by=by, answered_at=now(),
                request_ids=[request_id] if request_id else [],
            )
            q.status = "answered"
            rec.answer = ans
            self._save(rec)
        self.events.append(
            "question.answered", f"回答问题 {question_id}：{choice or ''} {text[:60]}".strip(),
            actor="user" if by == "user" else by, data={"question_id": question_id, "choice": choice},
        )
        return ans

    def answer_of(self, question_id: str) -> Answer | None:
        return self.record(question_id).answer
