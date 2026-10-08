"""CLI 走一遍 README 里的流程（后台用进程内的 TestClient 代替）。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from airesearcher.cli import main as cli
from airesearcher.server.app import create_app

runner = CliRunner()


@pytest.fixture
def server(air_home, monkeypatch):
    with TestClient(create_app(home=air_home, engine_kwargs={"retry_delay": 0})) as c:
        monkeypatch.setattr(cli, "_client", c)
        real_sleep = time.sleep
        monkeypatch.setattr(cli.time, "sleep", lambda s: real_sleep(min(s, 0.3)))
        yield c


def run(*args):
    r = runner.invoke(cli.app, list(args), env={"COLUMNS": "160"})
    assert r.exit_code == 0, r.output
    return r.output


def test_cli_walkthrough(server):
    out = run("new", "--idea", "比较基线、主方法和消融在冒烟任务上的得分", "--smoke-seconds", "0.3")
    assert "已创建项目" in out
    out = run("status", "--wait", "--timeout", "60")
    assert "等待审批 idea" in out and "air approve ap-0001" in out
    assert "air approve" in run("show", "ap-0001")
    out = run("revise", "ap-0001", "-m", "请写得更具体")
    run("status", "--wait")
    assert "请写得更具体" in run("show", "ap-0002")
    run("approve", "ap-0002")
    out = run("status", "--wait")
    assert "ap-0003" in out
    run("approve", "ap-0003")
    out = run("status", "--wait", "--timeout", "90")
    assert "过程日志" in out
    assert "succeeded" not in run("runs")  # 显示中文状态
    assert "成功" in run("runs")
    run("approve", "ap-0004")
    out = run("status", "--wait")
    assert "最终手稿" in out
    assert "PDF" in run("show", "ap-0005")
    out = run("approve", "ap-0005")
    out = run("status")
    assert "已完成" in out and "air reopen" in out
    assert "已完成" in run("list")


def test_cli_question_and_errors(server):
    run("new", "--idea", "测试失败运行的提问", "--smoke-seconds", "0.3", "--smoke-fail", "E1-seed=1:exit1")
    run("status", "--wait")
    run("approve", "ap-0001")
    run("status", "--wait")
    run("approve", "ap-0002")
    out = run("status", "--wait", "--timeout", "90")
    assert "air answer skip" in out
    assert "E1-seed=1" in run("question")
    out = run("answer", "skip")
    assert "已回答" in out
    r = runner.invoke(cli.app, ["approve", "ap-0999"])
    assert r.exit_code == 1 and "找不到审批" in r.output
    r = runner.invoke(cli.app, ["pause", "p-nope"])
    assert r.exit_code == 1 and "找不到项目" in r.output


def test_dev_commands(tmp_path):
    ws = tmp_path / "ws"
    run("dev", "new-workspace", str(ws), "--idea", "比较两种小模型在情感分类上的表现", "--impl", "idea=example")
    out = run("dev", "run-stage", "example", "--workspace", str(ws), "--steps", "3", "--fake-llm")
    assert "NeedsApproval(idea" in out and "等待状态" in out
    out = run("dev", "run-stage", "example", "--workspace", str(ws), "--steps", "2", "--fake-llm",
              "--auto-approve")
    assert "自动批准" in out


def test_dev_check_sample_project():
    from airesearcher.testing.sample import SAMPLE_PROJECT

    out = run("dev", "check", str(SAMPLE_PROJECT))
    assert "全部通过" in out


def test_models_commands(server, air_home, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = run("models")
    assert "claude-fast" in out and "未设置" in out and "ANTHROPIC_API_KEY" in out
    r = runner.invoke(cli.app, ["models", "test", "claude-fast"])
    assert r.exit_code == 1 and "ANTHROPIC_API_KEY" in r.output
    out = run("models", "init")
    assert (air_home / "models.yaml").exists() and (air_home / ".env").exists()
    (air_home / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-fake\n", encoding="utf-8")
    out = run("models")
    assert "已设置" in out and "sk-ant-fake" not in out
