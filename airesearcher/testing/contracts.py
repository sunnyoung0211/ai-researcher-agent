"""契约检查：项目目录里的交接文件是否符合冻结的格式（详细设计 1 第 11 节）。

“契约”是模块之间通过文件交接时约定的格式。这里把约定写成自动检查，三处共用：
- tests/contracts/：用样例项目 fixtures/sample_project/ 跑，CI 每次都检查；
- 端到端测试：检查假实现（以及以后的真实实现）实际写出的文件；
- 组员自查：air dev check <项目目录>，或 python -m airesearcher.testing.contracts <项目目录>。

每个检查只看“格式与引用关系”（能否被数据模型读取、引用的东西是否存在、数字能否从原始数据重算），
不判断内容好坏。文件还不存在的部分会标为“跳过”，所以做到一半的项目也能检查。
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from pydantic import BaseModel, ValidationError

from airesearcher.core.archive import Archive
from airesearcher.core.errors import AirError
from airesearcher.core.events import Events
from airesearcher.core.fsutil import read_json
from airesearcher.core.models.aggregate import Aggregate
from airesearcher.core.models.approval import Approval
from airesearcher.core.models.artifact import ArtifactVersion
from airesearcher.core.models.budget import BudgetEntry
from airesearcher.core.models.claim import CheckReport, Claim, CompileReport
from airesearcher.core.models.event import Event
from airesearcher.core.models.figure import Figure
from airesearcher.core.models.idea import IdeaScores, parse_selected_md
from airesearcher.core.models.literature import PaperRecord, ReadingCard, SearchSnapshot
from airesearcher.core.models.plan import PlanTask, PrecheckItem, TaskConfig, parse_plan_md
from airesearcher.core.models.project import ProjectConfig
from airesearcher.core.models.question import QuestionRecord
from airesearcher.core.models.run import (
    TERMINAL_RUN_STATES,
    MetricRecord,
    Resources,
    RunRecord,
    RunState,
    RunStatus,
)
from airesearcher.core.workspace import resolve_task_dir

RUN_ID_RE = re.compile(r"^r-\d{8}-\d{6}-[0-9a-f]{4}$")
CITE_KEY_RE = re.compile(r"^[a-z0-9]+$")
AIRVAL_RE = re.compile(r"\\airval\{([^}]*)\}")
AIRVAL_DEF_RE = re.compile(r"\\csname airval@([^\\]*)\\endcsname")
CITE_RE = re.compile(r"\\cite[pt]?\*?(?:\[[^\]]*\])?\{([^}]*)\}")
BIB_KEY_RE = re.compile(r"@\w+\{([^,\s]+),")
ENV_KEYS = ("env_hash", "os", "python", "command", "unknown")


@dataclass
class Section:
    name: str
    owner: str  # 提供方：主干 / 文献 / 实验 / 论文
    checked: int = 0  # 检查过的文件或条目数
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None

    def err(self, msg: str) -> None:
        self.errors.append(msg)


@dataclass
class ContractReport:
    root: Path
    sections: list[Section]

    @property
    def ok(self) -> bool:
        return all(not s.errors for s in self.sections)

    def errors(self) -> list[str]:
        return [f"[{s.name}] {e}" for s in self.sections for e in s.errors]

    def text(self) -> str:
        lines = [f"契约检查：{self.root}"]
        for s in self.sections:
            if s.skipped:
                lines.append(f"  -  {s.name}（{s.owner}）：跳过——{s.skipped}")
            elif s.errors:
                lines.append(f"  ✗  {s.name}（{s.owner}）：{len(s.errors)} 个问题")
                lines += [f"       - {e}" for e in s.errors[:20]]
                if len(s.errors) > 20:
                    lines.append(f"       ……另有 {len(s.errors) - 20} 个")
            else:
                lines.append(f"  ✓  {s.name}（{s.owner}）：通过（{s.checked} 项）")
        lines.append("结论：" + ("全部通过" if self.ok else f"有 {len(self.errors())} 个问题"))
        return "\n".join(lines)


# ---------------------------------------------------------------- 小工具
def _validate(model: type[BaseModel], data: Any, where: str, s: Section) -> Any:
    s.checked += 1
    try:
        return model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        s.err(f"{where}：不符合 {model.__name__}（{loc}：{first['msg']}；共 {e.error_count()} 处）")
        return None


def _json(path: Path, root: Path, s: Section) -> Any:
    try:
        return read_json(path)
    except (ValueError, OSError) as e:
        s.err(f"{path.relative_to(root).as_posix()}：不是合法 JSON（{e}）")
        return None


def _jsonl(path: Path, root: Path, s: Section) -> list[tuple[int, dict]]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append((i, json.loads(line)))
        except ValueError:
            s.err(f"{path.relative_to(root).as_posix()} 第 {i} 行：不是合法 JSON")
    return out


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _project(root: Path) -> ProjectConfig | None:
    try:
        return ProjectConfig.model_validate(yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8")))
    except (OSError, ValidationError, yaml.YAMLError):
        return None


def _task(cfg: ProjectConfig | None) -> TaskConfig | None:
    if cfg is None:
        return None
    try:
        d = resolve_task_dir(cfg.task)
        return TaskConfig.model_validate(yaml.safe_load((d / "task.yaml").read_text(encoding="utf-8")))
    except (FileNotFoundError, ValidationError):
        return None


def _papers(root: Path) -> dict[str, PaperRecord]:
    from airesearcher.services.literature import load_papers

    return load_papers(root)


# ---------------------------------------------------------------- 主干
def check_backbone(root: Path, s: Section) -> None:
    """project.yaml、事件、预算、审批、问题、检查点、产物存档。"""
    if _validate(ProjectConfig, yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8")),
                 "project.yaml", s) is None:
        return
    last = 0
    log = root / "research_log.jsonl"
    for i, raw in _jsonl(log, root, s) if log.exists() else []:
        ev = _validate(Event, raw, f"research_log.jsonl 第 {i} 行", s)
        if ev is not None:
            if ev.seq <= last:
                s.err(f"research_log.jsonl 第 {i} 行：seq {ev.seq} 不是递增的")
            last = ev.seq
    budget = root / "budget.jsonl"
    for i, raw in _jsonl(budget, root, s) if budget.exists() else []:
        _validate(BudgetEntry, raw, f"budget.jsonl 第 {i} 行", s)
    arch = Archive(root, Events(root))
    for p in sorted((root / "approvals").glob("*.json")):
        a = _validate(Approval, _json(p, root, s), _rel(p, root), s)
        if a is None:
            continue
        if a.approval_id != p.stem:
            s.err(f"{_rel(p, root)}：approval_id {a.approval_id} 与文件名不一致")
        for ref in [a.target, *a.extra_refs]:
            if not any(v.ref == ref for v in arch.versions(ref.artifact_id)):
                s.err(f"{_rel(p, root)}：引用的产物版本不存在 {ref.artifact_id} v{ref.version}")
    for p in sorted((root / "questions").glob("*.json")):
        _validate(QuestionRecord, _json(p, root, s), _rel(p, root), s)
    ck = root / ".state" / "checkpoint.json"
    if ck.exists():
        from airesearcher.engine.checkpoint import Checkpoint

        _validate(Checkpoint, _json(ck, root, s), ".state/checkpoint.json", s)
    for aid in arch.artifact_ids():
        vfile = arch.dir / quote(aid, safe="") / "versions.jsonl"
        rows = []
        for i, raw in _jsonl(vfile, root, s):
            r = _validate(ArtifactVersion, raw, f".archive/{aid}/versions.jsonl 第 {i} 行", s)
            if r is not None:
                rows.append(r)
        if [r.ref.version for r in rows] != list(range(1, len(rows) + 1)):
            s.err(f"{aid}：版本号不是从 1 开始连续递增")
        for r in rows:
            try:
                arch.get(r.ref)  # 校验存档内容的哈希
            except (AirError, OSError) as e:
                s.err(f"{aid} v{r.ref.version}：存档校验失败（{e}）")


# ---------------------------------------------------------------- 文献（详细设计 2）
def check_literature(root: Path, s: Section) -> None:
    lit = root / "idea" / "literature.jsonl"
    sel = root / "idea" / "selected.md"
    if not lit.exists() and not sel.exists():
        s.skipped = "还没有 idea/literature.jsonl 和 idea/selected.md"
        return
    papers: dict[str, PaperRecord] = {}
    for i, raw in _jsonl(lit, root, s) if lit.exists() else []:
        rec = _validate(PaperRecord, raw, f"idea/literature.jsonl 第 {i} 行", s)
        if rec is not None:
            papers[rec.paper_id] = rec  # 同一 paper_id 以最后一行为准
            if not CITE_KEY_RE.match(rec.citation_key):
                s.err(f"{rec.paper_id}：citation_key {rec.citation_key!r} 只能包含 a-z0-9")
    keys = [p.citation_key for p in papers.values()]
    if len(keys) != len(set(keys)):
        s.err("literature.jsonl：citation_key 有重复")
    snapshots = set()
    for p in sorted((root / "idea" / "search_snapshots").glob("*.json")):
        snap = _validate(SearchSnapshot, _json(p, root, s), _rel(p, root), s)
        if snap is not None:
            snapshots.add(snap.snapshot_id)
    for pid, rec in papers.items():
        if snapshots and rec.source != "manual" and rec.snapshot_id not in snapshots:
            s.err(f"{pid}：snapshot_id {rec.snapshot_id} 没有对应的检索快照文件")
    for p in sorted((root / "idea" / "reading_cards").glob("*.json")):
        card = _validate(ReadingCard, _json(p, root, s), _rel(p, root), s)
        if card is not None and card.paper_id not in papers:
            s.err(f"{_rel(p, root)}：paper_id {card.paper_id} 不在文献记录中")
    if not sel.exists():
        return
    s.checked += 1
    text = sel.read_text(encoding="utf-8")
    try:
        idea = parse_selected_md(text)
    except (ValueError, ValidationError) as e:
        s.err(f"idea/selected.md：YAML 区块不能被 SelectedIdea 解析（{str(e).splitlines()[0]}）")
        return
    cited = set(idea.citations) | {c for b in idea.baselines for c in b.citations} | \
        {c for m in idea.methods for c in m.citations}
    cited |= set(re.findall(r"\[((?:doi|arxiv|s2):[^\]\s]+)\]", text))
    for pid in sorted(cited - set(papers)):
        s.err(f"idea/selected.md：引用 {pid} 不在 literature.jsonl 中（禁止编造引用）")
    cfg = _project(root)
    if cfg is not None and idea.task.get("task_config") != cfg.task:
        s.err(f"idea/selected.md：task.task_config={idea.task.get('task_config')!r} 与 project.yaml 的 task 不一致")
    task = _task(cfg)
    if task is not None:
        allowed = {m.name for m in task.metrics}
        for m in idea.metrics:
            if m.name not in allowed:
                s.err(f"idea/selected.md：指标 {m.name} 不在任务 {task.name} 的 metrics 中")
    for ref_name in ("scores_ref", "gap_table_ref"):
        if not (root / getattr(idea, ref_name)).exists():
            s.err(f"idea/selected.md：{ref_name} 指向的文件不存在：{getattr(idea, ref_name)}")
    scores = root / "idea" / "scores.json"
    if scores.exists():
        _validate(IdeaScores, _json(scores, root, s), "idea/scores.json", s)


# ---------------------------------------------------------------- 实验：计划（详细设计 3 第 4 节）
def check_plan(root: Path, s: Section) -> None:
    plan_md = root / "plan" / "experiment_plan.md"
    if not plan_md.exists():
        s.skipped = "还没有 plan/experiment_plan.md"
        return
    s.checked += 1
    try:
        plan = parse_plan_md(plan_md.read_text(encoding="utf-8"))
    except (ValueError, ValidationError) as e:
        s.err(f"plan/experiment_plan.md：YAML 区块不能被 ExperimentPlan 解析（{str(e).splitlines()[0]}）")
        return
    arch = Archive(root, Events(root))
    if not any(v.ref == plan.idea_ref for v in arch.versions(plan.idea_ref.artifact_id)):
        s.err(f"experiment_plan.md：idea_ref 指向的版本不存在（{plan.idea_ref.artifact_id} v{plan.idea_ref.version}）")
    cfg = _project(root)
    if cfg is not None and plan.task_config != cfg.task:
        s.err(f"experiment_plan.md：task_config={plan.task_config!r} 与 project.yaml 的 task 不一致")
    exp_ids = [e.id for e in plan.experiments]
    if len(exp_ids) != len(set(exp_ids)):
        s.err("experiment_plan.md：实验 ID 有重复")
    for c in plan.comparisons:
        for e in c.experiments:
            if e not in exp_ids:
                s.err(f"experiment_plan.md：比较 {c.id} 引用了不存在的实验 {e}")
    for e in plan.experiments:
        for d in e.depends_on:
            if d not in exp_ids:
                s.err(f"experiment_plan.md：实验 {e.id} 依赖不存在的实验 {d}")
    tasks_p = root / "plan" / "tasks.json"
    if tasks_p.exists():
        raw = _json(tasks_p, root, s) or []
        tasks = [t for i, x in enumerate(raw) if (t := _validate(PlanTask, x, f"plan/tasks.json[{i}]", s))]
        keys = [t.task_key for t in tasks]
        if len(keys) != len(set(keys)):
            s.err("plan/tasks.json：task_key 有重复")
        for t in tasks:
            if t.experiment_id not in exp_ids:
                s.err(f"plan/tasks.json：{t.task_key} 属于不存在的实验 {t.experiment_id}")
    pre = root / "plan" / "precheck.json"
    if pre.exists():
        for i, x in enumerate(_json(pre, root, s) or []):
            _validate(PrecheckItem, x, f"plan/precheck.json[{i}]", s)


# ---------------------------------------------------------------- 实验：运行目录（详细设计 3 第 5 节）
def check_runs(root: Path, s: Section) -> None:
    run_dirs = sorted(p for p in (root / "runs").glob("*") if p.is_dir())
    if not run_dirs:
        s.skipped = "还没有运行"
        return
    for d in run_dirs:
        where = f"runs/{d.name}"
        if not (d / "run.json").exists():
            s.err(f"{where}：缺少 run.json")
            continue
        rec = _validate(RunRecord, _json(d / "run.json", root, s), f"{where}/run.json", s)
        st = _validate(RunStatus, _json(d / "status.json", root, s), f"{where}/status.json", s) \
            if (d / "status.json").exists() else None
        if rec is None:
            continue
        if rec.run_id != d.name:
            s.err(f"{where}/run.json：run_id {rec.run_id} 与目录名不一致")
        if not RUN_ID_RE.match(rec.run_id):
            s.err(f"{where}：run_id 格式应为 r-YYYYMMDD-HHMMSS-xxxx")
        if st is None:
            s.err(f"{where}：缺少 status.json")
            continue
        metrics = []
        last = 0
        mpath = d / "metrics.jsonl"
        for i, raw in _jsonl(mpath, root, s) if mpath.exists() else []:
            m = _validate(MetricRecord, raw, f"{where}/metrics.jsonl 第 {i} 行", s)
            if m is not None:
                if m.seq <= last:
                    s.err(f"{where}/metrics.jsonl 第 {i} 行：seq 不是递增的")
                last = m.seq
                metrics.append(m)
        if st.state in TERMINAL_RUN_STATES:
            if (d / "resources.json").exists():
                _validate(Resources, _json(d / "resources.json", root, s), f"{where}/resources.json", s)
            else:
                s.err(f"{where}：已结束的运行缺少 resources.json")
        if st.state == RunState.succeeded:
            missing = set(rec.required_metrics) - {m.name for m in metrics}
            if missing:
                s.err(f"{where}：成功的运行缺少必需指标 {sorted(missing)}")
        if st.state == RunState.failed and not st.failure_reason:
            s.err(f"{where}：失败的运行没有 failure_reason")
        env = d / "environment.json"
        if env.exists():
            data = _json(env, root, s) or {}
            for k in ENV_KEYS:
                if k not in data:
                    s.err(f"{where}/environment.json：缺少字段 {k}")
        elif st.state not in (RunState.created, RunState.preparing):
            s.err(f"{where}：缺少 environment.json")
        if rec.retry_of and not (root / "runs" / rec.retry_of).exists():
            s.err(f"{where}：retry_of 指向不存在的运行 {rec.retry_of}")


# ---------------------------------------------------------------- 实验：汇总结果（详细设计 3 第 8 节）
def check_aggregates(root: Path, s: Section) -> None:
    from airesearcher.services.runs import recompute_aggregate

    files = sorted((root / "artifacts" / "aggregates").glob("*.json"))
    if not files:
        s.skipped = "还没有 artifacts/aggregates/*.json"
        return
    for p in files:
        agg = _validate(Aggregate, _json(p, root, s), _rel(p, root), s)
        if agg is None:
            continue
        if agg.aggregate_id != p.stem:
            s.err(f"{_rel(p, root)}：aggregate_id {agg.aggregate_id} 与文件名不一致")
        for ext in (".csv", ".md"):
            if not p.with_suffix(ext).exists():
                s.err(f"{_rel(p, root)}：缺少同名的 {ext} 文件")
        for row in agg.rows:
            for rid in row.run_ids:
                if not (root / "runs" / rid).exists():
                    s.err(f"{agg.aggregate_id}：引用了不存在的运行 {rid}")
        try:
            again = recompute_aggregate(root, agg.aggregate_id)
        except Exception as e:  # 重算失败本身就是问题
            s.err(f"{agg.aggregate_id}：无法从原始指标重算（{e}）")
            continue
        old = {json.dumps(r.group, sort_keys=True): r for r in agg.rows}
        new = {json.dumps(r.group, sort_keys=True): r for r in again.rows}
        if set(old) != set(new):
            s.err(f"{agg.aggregate_id}：重算得到的分组与文件不一致")
        for k in set(old) & set(new):
            a, b = old[k], new[k]
            if a.n != b.n or abs(a.mean - b.mean) > 1e-6:
                s.err(f"{agg.aggregate_id} 分组 {k}：文件中 n={a.n}, mean={a.mean}，重算 n={b.n}, mean={b.mean}")


# ---------------------------------------------------------------- 论文（详细设计 4 第 3、6 节）
def check_figures(root: Path, s: Section) -> None:
    files = sorted((root / "artifacts" / "figures").glob("*/figure.json"))
    if not files:
        s.skipped = "还没有图表"
        return
    for p in files:
        fig = _validate(Figure, _json(p, root, s), _rel(p, root), s)
        if fig is None:
            continue
        if fig.fig_id != p.parent.name:
            s.err(f"{_rel(p, root)}：fig_id 与目录名不一致")
        if not (root / "artifacts" / "aggregates" / f"{fig.spec.aggregate_id}.json").exists():
            s.err(f"{fig.fig_id}：数据来源 {fig.spec.aggregate_id} 不存在")
        for out in fig.outputs:
            if not (root / out).exists():
                s.err(f"{fig.fig_id}：输出文件不存在 {out}")


def check_paper(root: Path, s: Section) -> None:
    paper = root / "paper"
    claims_p = paper / "claims.jsonl"
    if not (paper / "main.tex").exists() and not claims_p.exists():
        s.skipped = "还没有 paper/"
        return
    claims: list[Claim] = []
    for i, raw in _jsonl(claims_p, root, s) if claims_p.exists() else []:
        c = _validate(Claim, raw, f"paper/claims.jsonl 第 {i} 行", s)
        if c is not None:
            claims.append(c)
    ids = [c.claim_id for c in claims]
    if len(ids) != len(set(ids)):
        s.err("paper/claims.jsonl：claim_id 有重复")
    for c in claims:
        if c.statement_kind == "interpretation" and c.hedge == "none":
            s.err(f"{c.claim_id}：解释类论断的 hedge 不能为 none")
        for v in c.values:
            if not (root / "artifacts" / "aggregates" / f"{v.aggregate_id}.json").exists():
                s.err(f"{c.claim_id}：数字 {v.key} 引用的汇总结果 {v.aggregate_id} 不存在")
        if not c.evidence and c.type != "method_fact":
            s.err(f"{c.claim_id}：没有任何证据链接")
    defined = set(AIRVAL_DEF_RE.findall((paper / "air_values.tex").read_text(encoding="utf-8"))) \
        if (paper / "air_values.tex").exists() else set()
    tex_files = [paper / "main.tex", *sorted((paper / "sections").glob("*.tex"))]
    used, cites = set(), set()
    for t in tex_files:
        if t.exists():
            body = t.read_text(encoding="utf-8")
            used |= set(AIRVAL_RE.findall(body))
            cites |= {k.strip() for m in CITE_RE.findall(body) for k in m.split(",") if k.strip()}
    for key in sorted(used - defined):
        s.err(f"正文用了 \\airval{{{key}}}，但 air_values.tex 中没有定义")
    for key in sorted({v.key for c in claims for v in c.values} - defined):
        s.err(f"claims.jsonl 中的数字键 {key} 没有写进 air_values.tex")
    bib = paper / "references.bib"
    bib_keys = set(BIB_KEY_RE.findall(bib.read_text(encoding="utf-8"))) if bib.exists() else set()
    lit_keys = {p.citation_key for p in _papers(root).values()}
    for key in sorted(bib_keys - lit_keys):
        s.err(f"references.bib 中的 {key} 不在文献记录中")
    for key in sorted(cites - bib_keys):
        s.err(f"正文 \\cite{{{key}}} 在 references.bib 中找不到")
    s.checked += len(tex_files)
    cr = paper / "build" / "compile_report.json"
    if cr.exists():
        _validate(CompileReport, _json(cr, root, s), "paper/build/compile_report.json", s)
    for p in sorted((paper / "review").glob("review_v*.json")):
        _validate(CheckReport, _json(p, root, s), _rel(p, root), s)


SECTIONS: list[tuple[str, str, Callable[[Path, Section], None]]] = [
    ("主干：项目、事件、审批、存档", "主干", check_backbone),
    ("文献：literature.jsonl / selected.md", "文献", check_literature),
    ("实验：计划", "实验", check_plan),
    ("实验：运行目录", "实验", check_runs),
    ("实验：汇总结果", "实验", check_aggregates),
    ("论文：图表", "论文", check_figures),
    ("论文：论文与论断", "论文", check_paper),
]


def check_project(root: Path | str, owners: list[str] | None = None) -> ContractReport:
    """检查一个项目目录。owners 可只检查某几位提供方，如 ["实验"]。"""
    root = Path(root).resolve()
    sections = []
    for name, owner, fn in SECTIONS:
        if owners and owner not in owners:
            continue
        s = Section(name=name, owner=owner)
        try:
            fn(root, s)
        except Exception as e:  # 检查程序本身出错也算作问题，并继续检查其他部分
            s.err(f"检查时出错：{type(e).__name__}: {e}")
        sections.append(s)
    return ContractReport(root=root, sections=sections)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法：python -m airesearcher.testing.contracts <项目目录>")
        return 2
    report = check_project(argv[0])
    print(report.text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
