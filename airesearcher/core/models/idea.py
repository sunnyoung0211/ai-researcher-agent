"""selected.md 结构（详细设计 2 第 4 节；文献同学起草，主干审核）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

from .frontmatter import split_front_matter


class Hypothesis(BaseModel):
    id: str
    statement: str
    falsified_if: str


class Baseline(BaseModel):
    id: str
    name: str
    why: str
    citations: list[str] = []


class Method(BaseModel):
    id: str
    name: str
    description: str
    citations: list[str] = []


class Metric(BaseModel):
    name: str
    direction: Literal["higher", "lower"]
    primary: bool


class DataSpec(BaseModel):
    name: str
    source: str
    license: str
    usage: str


class SelectedIdea(BaseModel):
    idea_id: str
    title: str
    research_question: str
    motivation: str
    hypotheses: list[Hypothesis]
    contributions: list[str]
    task: dict  # {domain, task_config, description}
    data: list[DataSpec]
    baselines: list[Baseline]
    methods: list[Method]
    metrics: list[Metric]
    budget: dict
    stopping_conditions: list[str]
    expected_difficulties: list[str] = []
    open_questions: list[str] = []
    citations: list[str]
    scores_ref: str
    gap_table_ref: str

    @field_validator("hypotheses", "baselines", "methods")
    @classmethod
    def _at_least_one(cls, v: list) -> list:
        if not v:
            raise ValueError("至少需要 1 项")
        return v

    @field_validator("metrics")
    @classmethod
    def _one_primary(cls, v: list[Metric]) -> list[Metric]:
        if sum(1 for m in v if m.primary) != 1:
            raise ValueError("metrics 中必须恰好有一个 primary")
        return v

    def primary_metric(self) -> Metric:
        return next(m for m in self.metrics if m.primary)


class DimensionScore(BaseModel):
    score: int  # 1-5
    rationale: str
    evidence: list[str]  # paper_id 或 card_id#i
    to_verify: bool  # 证据不足时为 True

    @field_validator("score")
    @classmethod
    def _range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("score 必须在 1~5 之间")
        return v


class IdeaScores(BaseModel):  # idea/scores.json（详细设计 2 第 3.5 节）
    novelty: DimensionScore
    feasibility: DimensionScore
    impact: DimensionScore
    testability: DimensionScore
    risk: DimensionScore  # 注意：分数越高 = 风险越高
    note: str = "评分仅供参考，不是唯一决策依据"
    model: str
    prompt_version: int


def parse_selected_md(text: str) -> SelectedIdea:
    """实验、论文阶段都用它读 selected.md。"""
    data, _body = split_front_matter(text)
    return SelectedIdea.model_validate(data)
