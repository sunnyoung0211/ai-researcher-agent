"""示例阶段（详细设计 1 第 10.1 节）：演示写一个阶段要用到的所有写法。

任务：读 goal，生成 example/summary.md，提交 idea 审批；被退回时按意见修改。
另有一个分支演示提问：goal 少于 10 个字时返回 Stop(question=...)（要求文字回答），
收到 ctx.answer 后把回答追加到目标描述中继续。

逐行解释见同目录的 README.md。
"""

from __future__ import annotations

from pydantic import BaseModel

from airesearcher.core.models.common import VersionRef
from airesearcher.core.models.question import Question
from airesearcher.engine.stage import Continue, NeedsApproval, StageContext, StepResult, Stop

SUMMARY = "example/summary.md"


class Summary(BaseModel):  # response_schema：模型输出必须符合这个结构
    title: str
    points: list[str]


def render_summary(s: Summary, goal: str) -> str:
    lines = [f"# {s.title}", "", f"> 研究目标：{goal.strip()}", ""]
    lines += [f"- {p}" for p in s.points]
    return "\n".join(lines) + "\n"


class ExampleStage:
    name = "example"

    def step(self, ctx: StageContext) -> StepResult:
        s = ctx.scratch.setdefault("example", {})  # 阶段私有进度，引擎会随检查点保存

        # 0. 收到回答（只在回答后的下一次 step 中出现一次）
        if ctx.answer and ctx.answer.question_id == s.get("asked"):
            s["extra_goal"] = ctx.answer.text
            s.pop("asked", None)

        goal = ctx.project.goal + (("\n补充：" + s["extra_goal"]) if s.get("extra_goal") else "")
        if len(goal.strip()) < 10:  # 演示提问：目标太短，请用户补充
            return Stop("研究目标太短", question=Question(text="研究目标太短了，请补充一两句具体想研究什么。"))

        # 1. 被退回：带着意见重写（同一个退回只处理一次）
        if ctx.feedback and s.get("handled_feedback") != ctx.feedback[-1].approval_id:
            s["handled_feedback"] = ctx.feedback[-1].approval_id
            s["revision_note"] = ctx.feedback[-1].decision_comment
            s.pop("draft_ref", None)

        # 2. 幂等：没做过才做
        if "draft_ref" not in s:
            ctx.progress("示例：生成摘要", 0, 2, "steps")
            out = ctx.llm.complete(role="example.writer", prompt_id="example/summarize",
                                   variables={"goal": goal, "note": s.get("revision_note", "")},
                                   response_schema=Summary)
            md = render_summary(out.parsed, goal)
            prev = ctx.archive.latest(SUMMARY)
            ref = ctx.archive.put(SUMMARY, "idea", md, parents=[prev] if prev else [],
                                  producer="agent:example/writer", note=s.get("revision_note", "初版"))
            s["draft_ref"] = ref.model_dump(mode="json")
            ctx.events.append("stage.decision", "生成摘要草稿", actor="agent:example")
            return Continue("草稿已保存")

        # 3. 提交审批（提交最新版本：若用户手工改过，最新版本就是人工修改版）
        ctx.progress("示例：等待审批", 2, 2, "steps")
        ref = ctx.archive.latest(SUMMARY) or VersionRef(**s["draft_ref"])
        return NeedsApproval(ref, "idea", summary="项目目标摘要", impact="无下游影响")


def create_stage() -> ExampleStage:
    return ExampleStage()
