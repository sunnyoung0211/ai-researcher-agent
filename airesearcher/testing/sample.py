"""样例项目 fixtures/sample_project/ 的取用（详细设计 1 第 10.2、10.3 节）。

样例项目是全组共用的测试数据。不要直接改它；需要一份可以随便改的副本时：

    from airesearcher.testing.sample import copy_sample_project
    project = copy_sample_project(tmp_path / "proj")

pytest 中可以直接用夹具 sample_project（见 tests/conftest.py）。
命令行：air dev new-workspace /tmp/ws --from fixtures/sample_project
"""

from __future__ import annotations

import shutil
from pathlib import Path

from airesearcher.core.project import Project
from airesearcher.core.workspace import repo_root

SAMPLE_PROJECT = repo_root() / "fixtures" / "sample_project"


def copy_sample_project(dest: Path) -> Project:
    """把样例项目复制到 dest 并打开。"""
    if not (SAMPLE_PROJECT / "project.yaml").exists():
        raise FileNotFoundError(f"找不到样例项目 {SAMPLE_PROJECT}（运行 python fixtures/build_sample.py 生成）")
    shutil.copytree(SAMPLE_PROJECT, dest, ignore=shutil.ignore_patterns(".gitignore"))
    return Project.open(dest)
