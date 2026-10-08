from __future__ import annotations

import pytest

from airesearcher.core.project import Project
from airesearcher.testing.sample import copy_sample_project


@pytest.fixture(autouse=True)
def air_home(tmp_path, monkeypatch):
    """每个测试用独立的 AIR_HOME，互不干扰；运行的心跳调快一些。"""
    home = tmp_path / "air"
    monkeypatch.setenv("AIR_HOME", str(home))
    monkeypatch.setenv("AIR_HEARTBEAT_S", "1")
    monkeypatch.delenv("AIR_STAGE_IMPL", raising=False)
    return home


@pytest.fixture
def project(air_home) -> Project:
    return Project.create(goal="比较两种方法在小数据上的表现", task="tasks/smoke", home=air_home)


@pytest.fixture
def sample_project(tmp_path) -> Project:
    """样例项目的一份副本（可以随便改，不影响 fixtures/sample_project/）。"""
    return copy_sample_project(tmp_path / "sample")
