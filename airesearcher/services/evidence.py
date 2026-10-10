"""证据核验服务（详细设计 4 第 8.4 节）。

本文件是主干写的**确定性桩实现**：只做了 8.1 中的第 1、3（STALE_EVIDENCE）、5（CONTRADICTED）、6 项，
足够让流程跑通、让 GUI 能显示核验报告。【论文】同学接手时补齐 NUM_UNBOUND、INVALID_RUN、
CONDITION_MISMATCH、N_MISMATCH、OVERCLAIM_SIGNIFICANCE、CITATION_ABSTRACT_ONLY，并实现 audit()。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from airesearcher.core.archive import Archive
from airesearcher.core.errors import NotFound
from airesearcher.core.events import Events
from airesearcher.core.fsutil import now, read_json, read_jsonl
from airesearcher.core.models.aggregate import Aggregate
from airesearcher.core.models.claim import CheckReport, Claim, ClaimCheck, EvidenceTrace, Issue, ValueRef
from airesearcher.core.models.common import VersionRef
from airesearcher.core.models.figure import Figure

from . import literature, runs

CITE_RE = re.compile(r"\\cite[pt]?\*?(?:\[[^\]]*\])?\{([^}]*)\}")


def render_value(value: float, fmt: dict) -> str:
    """按 ValueRef.fmt 渲染数字：scale、decimals、suffix。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "??"
    v = value * float(fmt.get("scale", 1))
    return f"{v:.{int(fmt.get('decimals', 1))}f}{fmt.get('suffix', '')}"


def load_claims(root: Path) -> list[Claim]:
    return [Claim.model_validate(r) for r in read_jsonl(Path(root) / "paper" / "claims.jsonl")]


def _support(issues: list[Issue], unverifiable: bool) -> str:
    codes = {i.code for i in issues}
    if "CONTRADICTED" in codes:
        return "contradicted"
    if unverifiable:
        return "unverifiable"
    if any(i.severity == "blocker" for i in issues) or "NO_EVIDENCE" in codes:
        return "unsupported"
    if any(i.severity == "major" for i in issues):
        return "partially_supported"
    return "supported"


def check(root: Path, paper_ref: VersionRef | None = None, mode: str = "full") -> CheckReport:
    """确定性检查（8.1 的 1~6 中已实现的部分）。主干 API 和评价脚本都可以直接调用。"""
    root = Path(root)
    archive = Archive(root, Events(root))
    if paper_ref is None:
        paper_ref = archive.latest("paper") or VersionRef(artifact_id="paper", version=0, sha256="")
    recomputed: dict[str, Aggregate | None] = {}

    def recompute(aid: str) -> Aggregate | None:
        if aid not in recomputed:
            try:
                recomputed[aid] = runs.recompute_aggregate(root, aid)
            except (NotFound, FileNotFoundError, ValueError):
                recomputed[aid] = None
        return recomputed[aid]

    checks: list[ClaimCheck] = []
    for c in load_claims(root):
        issues: list[Issue] = []
        rec_values: dict[str, str] = {}
        unverifiable = False
        for v in c.values:
            agg = recompute(v.aggregate_id)
            if agg is None:
                unverifiable = True
                continue
            latest = archive.latest(f"artifacts/aggregates/{v.aggregate_id}.json")
            if latest and latest.version != v.aggregate_version:
                issues.append(Issue(code="STALE_EVIDENCE", severity="major", claim_id=c.claim_id,
                                    location=c.location or {}, sentence=c.text,
                                    detail=f"引用的是 {v.aggregate_id} v{v.aggregate_version}，"
                                           f"最新为 v{latest.version}"))
            try:
                val = runs.resolve_value(root, v.aggregate_id, v.group, v.metric, v.stat, v.contrast, aggregate=agg)
                rendered = render_value(val, v.fmt)
            except (NotFound, ValueError):
                rendered = "??"
            rec_values[v.key] = rendered
            if rendered != v.rendered:
                issues.append(Issue(code="NUM_MISMATCH", severity="blocker", claim_id=c.claim_id,
                                    location=c.location or {}, sentence=c.text,
                                    detail=f"文中 {v.rendered}，重算为 {rendered}（{v.key}）"))
        if c.comparison is not None and c.values:
            agg = recompute(c.values[0].aggregate_id)
            if agg is not None:
                try:
                    diff = runs.resolve_value(root, agg.aggregate_id, {}, c.comparison.metric, "diff_mean",
                                              {"a": c.comparison.a, "b": c.comparison.b}, aggregate=agg)
                except (NotFound, ValueError):
                    diff = None
                if diff is not None and ((c.comparison.asserted == "a>b" and diff <= 0)
                                         or (c.comparison.asserted == "a<b" and diff >= 0)):
                    issues.append(Issue(code="CONTRADICTED", severity="blocker", claim_id=c.claim_id,
                                        location=c.location or {}, sentence=c.text,
                                        detail=f"论断 {c.comparison.asserted}，但均值差为 {diff:+.4f}"))
        checks.append(ClaimCheck(claim_id=c.claim_id, support=_support(issues, unverifiable), issues=issues,
                                 recomputed=rec_values))

    unbound: list[Issue] = []
    sections = root / "paper" / "sections"
    for tex in sorted(sections.glob("*.tex")) if sections.exists() else []:
        for lineno, line in enumerate(tex.read_text(encoding="utf-8").splitlines(), 1):
            for m in CITE_RE.finditer(line):
                for key in (k.strip() for k in m.group(1).split(",")):
                    if key and literature.resolve_citation_key(root, key) is None:
                        unbound.append(Issue(code="CITATION_NOT_IN_ARCHIVE", severity="blocker",
                                             location={"file": tex.relative_to(root).as_posix(), "line": lineno},
                                             sentence=line.strip(), detail=f"引用键 {key} 不在文献记录中"))

    counts: dict[str, int] = {"blocker": 0, "major": 0, "minor": 0}
    for i in [*(i for c in checks for i in c.issues), *unbound]:
        counts[i.severity] += 1
    for c in checks:
        counts[c.support] = counts.get(c.support, 0) + 1
    compile_report = root / "paper" / "build" / "compile_report.json"
    compile_ok = bool(read_json(compile_report).get("ok")) if compile_report.exists() else False
    return CheckReport(paper_ref=paper_ref, mode=mode if mode in ("full", "audit") else "full", claims=checks,
                       unbound_issues=unbound, counts=counts, compile_ok=compile_ok, created_at=now())


def trace(root: Path, claim_id: str) -> EvidenceTrace:
    """组装一条论断的证据链：论断 → 图表 → 汇总（含重算值）→ 运行 → 文献。"""
    root = Path(root)
    claims = {c.claim_id: c for c in load_claims(root)}
    if claim_id not in claims:
        raise NotFound(f"找不到论断 {claim_id}")
    c = claims[claim_id]
    aggs, run_ids, figures, papers = [], [], [], []
    for v in c.values:
        try:
            agg = runs.load_aggregate(root, v.aggregate_id)
        except NotFound:
            continue
        val: ValueRef = v
        try:
            recomputed = render_value(
                runs.resolve_value(root, v.aggregate_id, v.group, v.metric, v.stat, v.contrast,
                                   aggregate=runs.recompute_aggregate(root, v.aggregate_id)), v.fmt)
        except (NotFound, ValueError):
            recomputed = "??"
        aggs.append({"aggregate_id": v.aggregate_id, "version": v.aggregate_version, "key": val.key,
                     "rendered": v.rendered, "recomputed": recomputed})
        for row in agg.rows:
            if row.group == v.group or v.stat in ("diff_mean", "p_value"):
                run_ids.extend(row.run_ids)
    for e in c.evidence:
        if e.type == "figure":
            p = root / "artifacts" / "figures" / e.ref / "figure.json"
            if p.exists():
                figures.append(read_json(p))
        elif e.type == "paper":
            papers.append(literature.verify_citation(root, e.ref).model_dump(mode="json"))
        elif e.type == "run":
            run_ids.append(e.ref)
    run_briefs = []
    for rid in dict.fromkeys(run_ids):
        try:
            rv = runs.load_run(root, rid, with_metrics=False)
        except NotFound:
            continue
        run_briefs.append({"run_id": rid, "state": rv.status.state.value, "code_commit": rv.record.code_commit,
                           "config_sha256": rv.record.config_sha256, "config_path": f"runs/{rid}/config.yaml",
                           "environment": f"runs/{rid}/environment.json"})
    report = check(root)
    cc = next((x for x in report.claims if x.claim_id == claim_id), None)
    return EvidenceTrace(claim=c, figures=figures, aggregates=aggs, runs=run_briefs, papers=papers, check=cc)


def audit(root: Path, llm: object) -> CheckReport:
    """评价用：从最终 PDF 正文中抽取论断并核验。TODO（论文同学，详细设计 4 第 13.3 节）。"""
    raise NotImplementedError("evidence.audit() 由论文同学实现")


# ---------------------------------------------------------------- ARCHIVE_INDEX.md（详细设计 4 第 10.2 节）
_CLAIM_WRAP = re.compile(r"^\\claim\{[^}]*\}\{(.*)\}$", re.S)
_AIRVAL = re.compile(r"\\airval\{([^}]*)\}")


def _cell(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _latest_review(root: Path) -> CheckReport | None:
    reviews = sorted((Path(root) / "paper" / "review").glob("review_v*.json"),
                     key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not reviews:
        return None
    try:
        return CheckReport.model_validate(read_json(reviews[-1]))
    except ValueError:
        return None


def archive_index(root: Path, project_id: str = "", title: str = "") -> str:
    """导出时附带的对照表：图表 → 汇总 → 运行编号、论断 → 证据、运行清单。只读文件。"""
    root = Path(root)
    arch = Archive(root, Events(root))
    lines = [f"# 研究档案索引：{title or project_id}", "",
             f"项目 `{project_id}`，生成于 {now().isoformat(timespec='seconds')}。", ""]
    refs = []
    for aid, name in (("idea/selected.md", "研究问题"), ("plan/experiment_plan.md", "实验计划"), ("paper", "论文")):
        ref = arch.latest(aid)
        if ref is not None:
            refs.append(f"- {name}：`{aid}` v{ref.version}（sha256 `{ref.sha256[:12]}`）")
    lines += refs + ([""] if refs else [])

    labels = runs.load_labels(root)
    lines += ["## 1. 图表 → 汇总结果 → 运行", "",
              "| 图 | 标题 | 汇总结果 | 每组样本数 | 运行 |", "|---|---|---|---|---|"]
    fig_dir = root / "artifacts" / "figures"
    figs = sorted(fig_dir.glob("*/figure.json")) if fig_dir.exists() else []
    for p in figs:
        try:
            fig = Figure.model_validate(read_json(p))
        except ValueError:
            continue
        n = ", ".join(f"{k}={v}" for k, v in fig.n_per_group.items())
        lines.append(f"| `{fig.fig_id}` | {_cell(fig.spec.title, 50)} | `{fig.spec.aggregate_id}` "
                     f"v{fig.aggregate_ref.version} | {n} | {', '.join(f'`{r}`' for r in fig.run_ids)} |")
    if not figs:
        lines.append("| （还没有图表） | | | | |")

    lines += ["", "## 2. 汇总结果 → 运行", "",
              "| 汇总 | 组 | 指标 | n | 均值 | 95% 区间 | 运行 |", "|---|---|---|---|---|---|---|"]
    agg_dir = root / "artifacts" / "aggregates"
    aggs = sorted(agg_dir.glob("*.json")) if agg_dir.exists() else []
    excluded: list[str] = []
    for p in aggs:
        try:
            agg = Aggregate.model_validate(read_json(p))
        except ValueError:
            continue
        for r in agg.rows:
            ci = f"[{r.ci95[0]:.4f}, {r.ci95[1]:.4f}]" if r.ci95 else "—"
            group = ", ".join(f"{k}={v}" for k, v in r.group.items())
            run_cells = ", ".join(f"`{x}`" + ("（可疑）" if x in r.suspicious else "") for x in r.run_ids)
            lines.append(f"| `{agg.aggregate_id}` | {group} | {r.metric} | {r.n} | {r.mean:.4f} | {ci} | {run_cells} |")
        excluded += [f"- `{agg.aggregate_id}` 排除 `{e['run_id']}`：{e['reason']}" for e in agg.excluded]
    if not aggs:
        lines.append("| （还没有汇总结果） | | | | | | |")
    if excluded:
        lines += ["", "排除的运行：", "", *excluded]

    lines += ["", "## 3. 论断 → 证据", "", "| 论断 | 章节 | 内容 | 证据 | 核验结论 |", "|---|---|---|---|---|"]
    review = _latest_review(root)
    verdict = {c.claim_id: c.support for c in review.claims} if review else {}
    try:
        claims = load_claims(root)
    except (ValueError, FileNotFoundError):
        claims = []
    for c in claims:
        m = _CLAIM_WRAP.match(c.text.strip())
        text = _AIRVAL.sub(lambda x: f"[{x.group(1)}]", m.group(1) if m else c.text).replace("\\%", "%")
        ev = ", ".join(f"{e.type}:`{e.ref}`" + (f" v{e.version}" if e.version else "") for e in c.evidence)
        lines.append(f"| `{c.claim_id}` | {c.section} | {_cell(text)} | {ev} | {verdict.get(c.claim_id, '未核验')} |")
    if not claims:
        lines.append("| （还没有论断） | | | | |")
    if review:
        lines += ["", f"核验报告：`paper/review/` 最新一版，问题数 {review.counts}。"]

    lines += ["", "## 4. 运行清单", "",
              "| 运行 | 任务 | 类型 | 状态 | 人工标注 | 代码提交 | 配置 sha256 | 重试自 |",
              "|---|---|---|---|---|---|---|---|"]
    for rv in runs.list_runs(root):
        rec, st = rv.record, rv.status
        lab = labels.get(rec.run_id)
        state = st.state.value + (f"（{st.failure_reason}）" if st.failure_reason else "")
        lines.append(f"| `{rec.run_id}` | {rec.task_key} | {rec.kind} | {state} | "
                     f"{(lab.label + '：' + _cell(lab.reason, 40)) if lab else ''} | `{rec.code_commit[:10]}` | "
                     f"`{rec.config_sha256[:12]}` | {rec.retry_of or ''} |")

    cited = sorted({pid for c in claims for e in c.evidence if e.type == "paper" for pid in [e.ref]})
    papers = literature.load_papers(root)
    if papers:
        lines += ["", "## 5. 文献", "", "| 文献 | 引用键 | 标题 | 链接 | 论文中引用 |", "|---|---|---|---|---|"]
        for pid, rec in sorted(papers.items()):
            lines.append(f"| `{pid}` | `{rec.citation_key}` | {_cell(rec.title, 60)} | {rec.url} | "
                         f"{'✓' if pid in cited else ''} |")
    return "\n".join(lines) + "\n"
