"""FakeLiterature：从 fixtures/literature_snapshot.json 返回检索结果，不联网（详细设计 1 第 10.3 节）。

接口与详细设计 2 第 5.2 节的 LiteratureSource 相同，文献同学写测试时可以直接替换真实的检索源：

    src = FakeLiterature()
    snapshot, papers = src.search("parameter-efficient fine-tuning low-resource", year_from=2020, limit=5)
    src.get("arxiv:2106.09685")            # 按 paper_id 取一篇，没有返回 None
    src.calls                              # 记录了每次 search 的查询词

匹配规则：快照文件的 queries 中写了这条查询词时按它返回；否则按查询词与标题、关键词、摘要的重合程度排序，
没有任何重合的论文不返回。同一查询总是得到同样的结果。
用 FakeLiterature(fail_queries={"..."}) 模拟某条查询检索失败（抛出 ConnectionError），测试重试和换源。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from airesearcher.core.fsutil import now
from airesearcher.core.models.literature import PaperRecord, SearchSnapshot
from airesearcher.core.workspace import repo_root

SNAPSHOT_FILE = "fixtures/literature_snapshot.json"
STOPWORDS = {"the", "and", "for", "with", "from", "into", "under", "over", "via", "are", "does", "how", "what",
             "when", "which", "than", "that", "this", "using", "based", "study", "effect", "effects"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in STOPWORDS}


def bibtex_of(p: dict) -> str:
    authors = " and ".join(p["authors"])
    fields = [f"  title = {{{p['title']}}}", f"  author = {{{authors}}}", f"  year = {{{p['year']}}}"]
    if p["venue_type"] == "journal":
        kind, fields = "article", [*fields, f"  journal = {{{p['venue']}}}"]
    elif p["venue_type"] in ("conference", "workshop"):
        kind, fields = "inproceedings", [*fields, f"  booktitle = {{{p['venue']}}}"]
    else:
        kind = "misc"
        if "arxiv" in p["ids"]:
            fields += [f"  eprint = {{{p['ids']['arxiv']}}}", "  archivePrefix = {arXiv}"]
    if "doi" in p["ids"]:
        fields.append(f"  doi = {{{p['ids']['doi']}}}")
    fields.append(f"  url = {{{p['url']}}}")
    return f"@{kind}{{{p['citation_key']},\n" + ",\n".join(fields) + "\n}"


class FakeLiterature:
    name = "fake"

    def __init__(self, path: str | Path | None = None, source: str = "semantic_scholar",
                 fail_queries: set[str] | None = None):
        self.path = Path(path) if path else repo_root() / SNAPSHOT_FILE
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.papers: dict[str, dict] = {p["paper_id"]: p for p in data["papers"]}
        self.fixed: dict[str, list[str]] = data.get("queries") or {}
        self.source = source  # 写进 PaperRecord.source；默认装作 Semantic Scholar，方便直接替换
        self.fail_queries = set(fail_queries or ())
        self.calls: list[str] = []

    def _record(self, p: dict, snapshot_id: str, query: str) -> PaperRecord:
        fields = {k: v for k, v in p.items() if k != "keywords"}
        return PaperRecord(**fields, access_level="abstract_only" if p.get("abstract") else "metadata_only",
                           fulltext_path=None, source=self.source, snapshot_id=snapshot_id, retrieved_at=now(),
                           queries=[query] if query else [], bibtex=bibtex_of(p))

    def _rank(self, query: str) -> list[str]:
        if query in self.fixed:
            return list(self.fixed[query])
        q = _tokens(query)
        scored = []
        for pid, p in self.papers.items():
            title, kw = _tokens(p["title"]), _tokens(" ".join(p.get("keywords", [])))
            score = 3 * len(q & kw) + 2 * len(q & title) + len(q & _tokens(p.get("abstract") or ""))
            if score:
                scored.append((-score, pid))
        return [pid for _, pid in sorted(scored)]

    def search(self, query: str, *, year_from: int | None = None, limit: int = 20) -> tuple[
            SearchSnapshot, list[PaperRecord]]:
        self.calls.append(query)
        if query in self.fail_queries:
            raise ConnectionError(f"FakeLiterature：模拟检索失败（{query}）")
        ids = [pid for pid in self._rank(query)
               if year_from is None or (self.papers[pid]["year"] or 0) >= year_from][:limit]
        sid = "ss-fake-" + hashlib.sha256(f"{query}|{year_from}|{limit}".encode()).hexdigest()[:8]
        papers = [self._record(self.papers[pid], sid, query) for pid in ids]
        raw: dict[str, Any] = {"fake": True, "total": len(ids),
                               "data": [{"paperId": pid, "title": self.papers[pid]["title"]} for pid in ids]}
        snap = SearchSnapshot(snapshot_id=sid, source=self.source, query=query,
                              params={"year_from": year_from, "limit": limit}, requested_at=now(), status="ok",
                              raw_response=raw, paper_ids=ids)
        return snap, papers

    def get(self, paper_id: str) -> PaperRecord | None:
        p = self.papers.get(paper_id)
        return self._record(p, "ss-fake-get", "") if p else None
