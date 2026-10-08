"""【文献】阶段的假实现（IdeaDrafting）。不联网、不调用模型。

产出的文件严格按详细设计 2 的格式（用 Pydantic 模型校验），文献同学可以直接用真实实现替换：
- idea/search_snapshots/ss-fake-0001.json   SearchSnapshot
- idea/literature.jsonl                     PaperRecord（3 篇固定论文）
- idea/scores.json、idea/gap_table.md        评分与差异表（简化）
- idea/selected.json → idea/selected.md     SelectedIdea（YAML 头部 + 正文）
然后提交 idea 审批；被退回时生成新版本，正文中写明用户意见。
"""

from __future__ import annotations

from airesearcher.core.fsutil import dumps, now
from airesearcher.core.models.frontmatter import render_front_matter
from airesearcher.core.models.idea import (
    Baseline,
    DataSpec,
    Hypothesis,
    IdeaScores,
    Method,
    Metric,
    SelectedIdea,
    parse_selected_md,
)
from airesearcher.core.models.literature import PaperRecord, SearchSnapshot
from airesearcher.engine.stage import Continue, NeedsApproval, StageContext, StepResult

from .common import feedback_note, load_task, new_feedback

SELECTED = "idea/selected.md"
LITERATURE = "idea/literature.jsonl"
SNAPSHOT_ID = "ss-fake-0001"

_PAPERS = [
    {
        "paper_id": "arxiv:2106.09685", "ids": {"arxiv": "2106.09685"},
        "title": "LoRA: Low-Rank Adaptation of Large Language Models",
        "authors": ["Edward J. Hu", "Yelong Shen", "Phillip Wallis", "Zeyuan Allen-Zhu", "Yuanzhi Li",
                    "Shean Wang", "Lu Wang", "Weizhu Chen"],
        "year": 2022, "venue": "ICLR", "venue_type": "conference", "url": "https://arxiv.org/abs/2106.09685",
        "abstract": "（假实现占位摘要）提出冻结预训练权重、只训练低秩分解矩阵的参数高效微调方法。",
        "citation_key": "hu2022lora",
    },
    {
        "paper_id": "arxiv:1810.04805", "ids": {"arxiv": "1810.04805"},
        "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "authors": ["Jacob Devlin", "Ming-Wei Chang", "Kenton Lee", "Kristina Toutanova"],
        "year": 2019, "venue": "NAACL", "venue_type": "conference", "url": "https://arxiv.org/abs/1810.04805",
        "abstract": "（假实现占位摘要）提出双向 Transformer 预训练与下游任务全量微调的范式。",
        "citation_key": "devlin2019bert",
    },
    {
        "paper_id": "arxiv:2006.05987", "ids": {"arxiv": "2006.05987"},
        "title": "Revisiting Few-sample BERT Fine-tuning",
        "authors": ["Tianyi Zhang", "Felix Wu", "Arzoo Katiyar", "Kilian Q. Weinberger", "Yoav Artzi"],
        "year": 2021, "venue": "ICLR", "venue_type": "conference", "url": "https://arxiv.org/abs/2006.05987",
        "abstract": "（假实现占位摘要）研究小样本条件下微调的不稳定性及改进做法。",
        "citation_key": "zhang2021revisiting",
    },
]


def _bibtex(p: dict) -> str:
    authors = " and ".join(p["authors"])
    return (f"@inproceedings{{{p['citation_key']},\n  title = {{{p['title']}}},\n  author = {{{authors}}},\n"
            f"  booktitle = {{{p['venue']}}},\n  year = {{{p['year']}}},\n  url = {{{p['url']}}}\n}}")


def fake_papers(query: str) -> list[PaperRecord]:
    ts = now()
    return [PaperRecord(**p, access_level="abstract_only", fulltext_path=None, source="manual",
                        snapshot_id=SNAPSHOT_ID, retrieved_at=ts, queries=[query], bibtex=_bibtex(p),
                        relevance={"score": 4, "reason": "假实现：固定论文"}, selected_for_reading=True)
            for p in _PAPERS]


def build_idea(ctx: StageContext, papers: list[PaperRecord]) -> SelectedIdea:
    task = load_task(ctx)
    goal = ctx.project.goal.strip()
    primary = next((m for m in task.metrics if m.required), task.metrics[0])
    methods = task.methods_available or ["baseline", "main", "ablation"]
    base, main = methods[0], methods[min(1, len(methods) - 1)]
    budget = {k: v.hard for k, v in ctx.project.budget.items() if v.hard is not None}
    return SelectedIdea(
        idea_id="idea-001",
        title=ctx.project.title,
        research_question=f"在任务 {task.name} 上，方法 {main} 的 {primary.name} 是否优于基线 {base}？"
                          f"（由研究目标生成：{goal[:80]}）",
        motivation=f"用户的研究目标：{goal}。已有工作对小数据场景下的结论不一致 [{papers[2].paper_id}]。",
        hypotheses=[
            Hypothesis(id="H1", statement=f"{main} 的平均 {primary.name} 高于 {base}。",
                       falsified_if=f"3 个种子的平均 {primary.name} 差值 ≤ 0。"),
        ],
        contributions=[f"在统一预算下比较 {base} 与 {main}，并做消融实验"],
        task={"domain": task.domain, "task_config": ctx.project.task, "description": task.description},
        data=[DataSpec(name=d.name, source=d.source, license=d.license or "未核实", usage="见任务配置")
              for d in task.data],
        baselines=[Baseline(id="B1", name=base, why="最常用的标准做法", citations=[papers[1].paper_id])],
        methods=[Method(id="M1", name=main, description=f"主方法 {main}", citations=[papers[0].paper_id])],
        metrics=[Metric(name=m.name, direction=m.direction, primary=(m.name == primary.name))
                 for m in task.metrics],
        budget=budget,
        stopping_conditions=["计划内实验全部完成", "预算达到硬上限", "连续 2 轮没有有效进展"],
        expected_difficulties=["小样本下方差大，需要多个种子"],
        open_questions=["（假实现）未检索真实文献，请在真实实现中替换"],
        citations=[p.paper_id for p in papers],
        scores_ref="idea/scores.json",
        gap_table_ref="idea/gap_table.md",
    )


def render_selected(idea: SelectedIdea, papers: list[PaperRecord], notes: list[str]) -> str:
    by_id = {p.paper_id: p for p in papers}
    lines = [
        "# 研究问题", idea.research_question, "",
        "# 动机", idea.motivation, "",
        "# 假设", *[f"- **{h.id}**：{h.statement}（证伪条件：{h.falsified_if}）" for h in idea.hypotheses], "",
        "# 贡献", *[f"- {c}" for c in idea.contributions], "",
        "# 任务与数据", f"- 任务配置：`{idea.task['task_config']}`（{idea.task['description']}）",
        *[f"- 数据 {d.name}：{d.source}（许可：{d.license}）" for d in idea.data], "",
        "# 基线与方法", *[f"- 基线 {b.id} {b.name}：{b.why} " + " ".join(f"[{c}]" for c in b.citations)
                          for b in idea.baselines],
        *[f"- 方法 {m.id} {m.name}：{m.description} " + " ".join(f"[{c}]" for c in m.citations)
          for m in idea.methods], "",
        "# 评价指标", *[f"- {m.name}（{'越高越好' if m.direction == 'higher' else '越低越好'}"
                         f"{'，主要指标' if m.primary else ''}）" for m in idea.metrics], "",
        "# 预期困难", *[f"- {d}" for d in idea.expected_difficulties], "",
        "# 预算与停止条件", f"- 预算：{idea.budget}", *[f"- {c}" for c in idea.stopping_conditions], "",
        "# 待确认问题", *[f"- {q}" for q in idea.open_questions], "",
        "# 参考文献",
        *[f"- [{pid}] {by_id[pid].authors[0].split()[-1]} et al. {by_id[pid].year}. {by_id[pid].title}. "
          f"{by_id[pid].url}" for pid in idea.citations if pid in by_id],
    ]
    if notes:
        lines += ["", "# 修订说明", *[f"- {n}" for n in notes]]
    return render_front_matter(idea.model_dump(mode="json"), "\n".join(lines) + "\n")


class FakeIdeaStage:
    name = "idea"

    def step(self, ctx: StageContext) -> StepResult:
        s = ctx.scratch.setdefault("idea", {})
        fb = new_feedback(ctx, s, "idea")
        if fb is not None:
            s.setdefault("notes", []).append(feedback_note(fb))
            s["phase"] = "write"
        if ctx.entry is not None and s.get("handled_entry") != ctx.entry.reason:
            s["handled_entry"] = ctx.entry.reason
            s.setdefault("notes", []).append(f"实验阶段要求修改 idea：{ctx.entry.reason}")
            s["phase"] = "write"
        phase = s.get("phase", "search")

        if phase == "search":
            ctx.progress("文献（假实现）：检索论文", 1, 3, "steps")
            query = ctx.project.goal.strip().splitlines()[0][:60]
            papers = fake_papers(query)
            snap = SearchSnapshot(snapshot_id=SNAPSHOT_ID, source="manual", query=query, params={"fake": True},
                                  requested_at=now(), status="ok", raw_response={"note": "假实现，没有联网"},
                                  paper_ids=[p.paper_id for p in papers])
            ctx.archive.put(f"idea/search_snapshots/{SNAPSHOT_ID}.json", "search_snapshot", dumps(snap),
                            producer="agent:idea/search")
            if ctx.archive.latest(LITERATURE) is None:
                text = "".join(p.model_dump_json() + "\n" for p in papers)
                ctx.archive.put(LITERATURE, "literature", text, producer="agent:idea/search", note="检索 3 篇论文")
            ctx.events.append("stage.decision", "（假实现）检索到 3 篇固定论文", actor="agent:idea")
            s["phase"] = "write"
            return Continue("检索完成")

        if phase == "write":
            ctx.progress("文献（假实现）：生成 selected.md", 2, 3, "steps")
            from airesearcher.services.literature import load_papers

            papers = list(load_papers(ctx.root).values())
            idea = build_idea(ctx, papers)
            notes = s.get("notes", [])
            lit = ctx.archive.latest(LITERATURE)
            scores = {d: {"score": 3, "rationale": "假实现：固定分数", "evidence": [], "to_verify": True}
                      for d in ("novelty", "feasibility", "impact", "testability", "risk")}
            idea_scores = IdeaScores.model_validate({**scores, "model": "fake", "prompt_version": 0})
            scores_ref = ctx.archive.put("idea/scores.json", "scores", dumps(idea_scores),
                                         producer="agent:idea/scorer")
            gap_ref = ctx.archive.put("idea/gap_table.md", "gap_table",
                                      "# 差异表（假实现）\n\n| 已有工作 | 本研究的不同 |\n|---|---|\n"
                                      f"| [{papers[0].paper_id}] | 在小数据、小模型上比较 |\n",
                                      producer="agent:idea/gap")
            ctx.archive.put("idea/selected.json", "idea", dumps(idea), producer="agent:idea/refine")
            md = render_selected(idea, papers, notes)
            parse_selected_md(md)  # 写入前校验：头部必须能被 SelectedIdea 解析
            prev = ctx.archive.latest(SELECTED)
            ctx.archive.put(SELECTED, "idea", md, parents=[r for r in (prev, lit, scores_ref, gap_ref) if r],
                            producer="agent:idea/writer", note=notes[-1] if notes else "初版")
            s["phase"] = "submit"
            return Continue("selected.md 已保存")

        ctx.progress("文献（假实现）：等待审批", 3, 3, "steps")
        ref = ctx.archive.latest(SELECTED)
        assert ref is not None
        extra = [r for r in (ctx.archive.latest("idea/scores.json"), ctx.archive.latest("idea/gap_table.md"),
                             ctx.archive.latest(LITERATURE)) if r]
        revised = "plan" in ctx.approved
        return NeedsApproval(
            target=ref, kind="idea",
            summary=f"（假实现）selected.md v{ref.version}：1 个假设，基线 B1、方法 M1，3 篇参考文献"
                    + ("；已按意见修改" if s.get("notes") else ""),
            impact="已批准的实验计划需要重新评估" if revised else "首次提交，无下游影响",
            extra_refs=extra,
        )


def create_stage() -> FakeIdeaStage:
    return FakeIdeaStage()
