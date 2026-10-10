"""导出 zip（详细设计 1 第 9.2 节 /export、详细设计 4 第 10.2 节）。只读项目文件，写到调用方给的 zip 路径。

- what="paper"：论文交付包——paper/（LaTeX 源文件、模板依赖、air_values.tex、claims.jsonl、核验报告、
  编译好的 PDF 和编译报告；不含 LaTeX 的中间文件）、artifacts/figures/（图、绘图脚本、数据）、ARCHIVE_INDEX.md；
- what="archive"：整个研究档案（项目目录全部内容，含 .archive/ 中的历史版本），不含工作区 .git 和锁文件。
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import Path

from . import evidence

WHAT = ("paper", "archive")
# 论文包里 paper/build/ 只保留这两个文件，其余是 LaTeX 中间文件
PAPER_BUILD_KEEP = {"build/main.pdf", "build/compile_report.json"}
ARCHIVE_SKIP = [".git/**", ".state/lock", "**/__pycache__/**", "*.pyc", "**/*.pyc"]


def _walk(base: Path) -> Iterator[Path]:
    """遍历目录下的普通文件；不跟随符号链接（防止把项目外的文件打包进去）。"""
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if not (d / n).is_symlink())
        for name in sorted(filenames):
            p = d / name
            if p.is_file() and not p.is_symlink():
                yield p


def paper_files(root: Path) -> list[tuple[Path, str]]:
    root = Path(root)
    out = []
    paper = root / "paper"
    if paper.exists():
        for p in _walk(paper):
            rel = p.relative_to(paper).as_posix()
            if rel.startswith("build/") and rel not in PAPER_BUILD_KEEP:
                continue
            out.append((p, f"paper/{rel}"))
    figs = root / "artifacts" / "figures"
    if figs.exists():
        out += [(p, p.relative_to(root).as_posix()) for p in _walk(figs)]
    return out


def archive_files(root: Path) -> list[tuple[Path, str]]:
    root = Path(root)
    out = []
    for p in _walk(root):
        rel = p.relative_to(root).as_posix()
        if any(fnmatchcase(rel, pat) for pat in ARCHIVE_SKIP):
            continue
        out.append((p, rel))
    return out


def write_zip(root: Path, what: str, dest: Path, project_id: str, title: str = "") -> int:
    """写 zip 到 dest，返回打包的文件数。zip 内统一放在 <project_id>-<what>/ 目录下。"""
    if what not in WHAT:
        raise ValueError(f"what 只能是 {' / '.join(WHAT)}")
    files = paper_files(root) if what == "paper" else archive_files(root)
    prefix = f"{project_id}-{what}"
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefix}/ARCHIVE_INDEX.md", evidence.archive_index(root, project_id, title))
        for src, rel in files:
            zf.write(src, f"{prefix}/{rel}")
    return len(files) + 1
