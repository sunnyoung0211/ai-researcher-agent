"""Read-only citation lookup and optional permission-checked URL accessibility checks."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import yaml

from airesearcher.core.fsutil import now, read_jsonl
from airesearcher.core.models.literature import CitationStatus, PaperRecord
from airesearcher.core.models.project import Permissions

LITERATURE = "idea/literature.jsonl"


def load_papers(root: Path) -> dict[str, PaperRecord]:
    """Read the append-only registry, keeping the latest record for each paper ID."""
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
            checked_at=now(), note="Not registered in idea/literature.jsonl; this citation must be flagged as invalid.",
        )
    note = ""
    if rec.access_level == "metadata_only" or not rec.url:
        status = "metadata_only"
    else:
        status = "verified"
    if online and status == "verified":
        reachable, note = _check_url(Path(root), rec.url)
        if not reachable:
            status = "source_unreachable"
    return CitationStatus(
        paper_id=paper_id, status=status, citation_key=rec.citation_key, bibtex=rec.bibtex, title=rec.title,
        url=rec.url, checked_at=now(), note=note,
    )


def _check_url(root: Path, url: str) -> tuple[bool, str]:
    """Check headers without reading bodies or changing project files or permissions."""
    try:
        config = root / "project.yaml"
        data = yaml.safe_load(config.read_text(encoding="utf-8")) if config.exists() else {}
        permissions = Permissions.model_validate((data or {}).get("permissions", {}))
    except (OSError, ValueError, AttributeError, yaml.YAMLError):
        return False, "Cannot read network permissions; no request was sent."
    deadline = time.monotonic() + 20
    redirects = 0
    method = "HEAD"
    try:
        target = httpx.URL(url)
        with httpx.Client(follow_redirects=False, headers={"User-Agent": "ai-researcher-agent/0.1"}) as client:
            while True:
                host = target.host.lower()
                if target.scheme not in {"https", "http"} or not host or target.userinfo:
                    return False, "Only HTTP(S) URLs without embedded credentials can be checked."
                # Apply the same domain/subdomain rule as the core network permission guard.
                if not any(host == d or host.endswith("." + d) for d in permissions.network.allow):
                    return False, "URL host is outside permissions.network.allow; no request sent to this host."
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, "URL accessibility check exceeded its time budget."
                timeout = httpx.Timeout(remaining, connect=min(5, remaining))
                with client.stream(method, target, timeout=timeout) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirects >= 5:
                            return False, "Missing redirect location or redirect limit exceeded."
                        target = target.join(location)
                        redirects += 1
                        continue
                    if method == "HEAD" and response.status_code in {405, 501}:
                        method = "GET"
                        continue
                    if response.is_success:
                        return True, f"URL accessible (HTTP {response.status_code}); content claims are not verified."
                    return False, f"Source returned HTTP {response.status_code}; accessibility could not be confirmed."
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        return False, f"URL accessibility check failed ({type(exc).__name__})."


def resolve_citation_key(root: Path, key: str) -> str | None:
    for pid, rec in load_papers(root).items():
        if rec.citation_key == key:
            return pid
    return None


def bibtex_for(root: Path, paper_ids: list[str]) -> str:
    papers = load_papers(root)
    entries = [papers[p].bibtex.strip() for p in paper_ids if p in papers]
    return "\n\n".join(entries) + ("\n" if entries else "")
