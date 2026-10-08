"""引用核验服务（详细设计 2 第 9 节）。只读文件、不调模型，论文阶段可以直接导入。

【文献】同学接手时：函数签名与判定规则按文档实现，online=True 的联网检查还没有做（TODO）。
"""

from __future__ import annotations

from pathlib import Path

from airesearcher.core.fsutil import now, read_jsonl
from airesearcher.core.models.literature import CitationStatus, PaperRecord

LITERATURE = "idea/literature.jsonl"


def load_papers(root: Path) -> dict[str, PaperRecord]:
    """literature.jsonl 只追加；同一 paper_id 以最后一行为准。"""
    out: dict[str, PaperRecord] = {}
    for row in read_jsonl(Path(root) / LITERATURE):
        try:
            rec = PaperRecord.model_validate(row)
        except ValueError:
            continue
        out[rec.paper_id] = rec
    return out


def verify_citation(root: Path, paper_id: str, online: bool = False) -> CitationStatus:
    papers = load_papers(root)
    rec = papers.get(paper_id)
    if rec is None:
        return CitationStatus(
            paper_id=paper_id, status="not_in_archive", citation_key=None, bibtex=None, title=None, url=None,
            checked_at=now(), note="不在 idea/literature.jsonl 中——论文中出现这种引用必须被标为错误",
        )
    if rec.access_level == "metadata_only" or not rec.url:
        status = "metadata_only"
    else:
        status = "verified"
    note = "online 检查尚未实现，只做了本地检查" if online else ""
    return CitationStatus(
        paper_id=paper_id, status=status, citation_key=rec.citation_key, bibtex=rec.bibtex, title=rec.title,
        url=rec.url, checked_at=now(), note=note,
    )


def resolve_citation_key(root: Path, key: str) -> str | None:
    for pid, rec in load_papers(root).items():
        if rec.citation_key == key:
            return pid
    return None


def bibtex_for(root: Path, paper_ids: list[str]) -> str:
    papers = load_papers(root)
    entries = [papers[p].bibtex.strip() for p in paper_ids if p in papers]
    return "\n\n".join(entries) + ("\n" if entries else "")
