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


def test_new_workspace_register_sample(tmp_path, air_home):
    """GUI 开发：把样例项目复制一份并登记，后台启动后能通过 API 读到。"""
    from airesearcher.core.workspace import load_registry
    from airesearcher.testing.sample import SAMPLE_PROJECT

    ws = tmp_path / "sample"
    out = run("dev", "new-workspace", str(ws), "--from", str(SAMPLE_PROJECT), "--register")
    assert "已登记到后台" in out
    [(pid, path)] = load_registry(air_home).items()
    assert path == str(ws.resolve())
    with TestClient(create_app(home=air_home, start_engines=False)) as c:
        st = c.get(f"/api/projects/{pid}").json()
        assert st["state"] == "ManuscriptPending"
        assert len(c.get(f"/api/projects/{pid}/runs").json()) == 11


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


@pytest.fixture
def sample_server(tmp_path, air_home, monkeypatch):
    """样例项目登记到后台（不启动引擎），CLI 通过它访问。"""
    from airesearcher.core.workspace import register_project
    from airesearcher.testing.sample import copy_sample_project

    p = copy_sample_project(tmp_path / "sample")
    register_project(p.project_id, p.root, home=air_home)
    with TestClient(create_app(home=air_home, start_engines=False)) as c:
        monkeypatch.setattr(cli, "_client", c)
        yield p


def test_label_and_cancel_run(sample_server):
    from airesearcher.services import runs as run_service

    p = sample_server
    rid = run_service.list_runs(p.root)[0].record.run_id
    out = run("label", rid, "invalid", "-m", "数据有泄漏")
    assert "无效" in out
    assert "无效" in run("runs")
    r = runner.invoke(cli.app, ["label", rid, "maybe"])
    assert r.exit_code == 1 and "trusted" in r.output
    out = run("cancel-run", rid)  # 已结束的运行：取消不生效，显示当前状态
    assert "已请求取消" in out and "成功" in out


def test_template_commands(sample_server, tmp_path):
    import zipfile

    z = tmp_path / "acl.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("main.tex", "\\documentclass{article}\n")
    assert "默认模板" in run("template", "list") and "还没有上传过模板" in run("template", "list")
    out = run("template", "upload", str(z))
    assert "tpl-01 v1" in out and "main.tex" in out
    out = run("template", "upload", str(z), "--to", "tpl-01")
    assert "tpl-01 v2" in out
    out = run("template", "list")
    assert "当前使用：tpl-01 v2" in out and "未检查" in out


def test_export_command(sample_server, tmp_path):
    import zipfile

    out = tmp_path / "paper.zip"
    assert "已导出" in run("export", "-o", str(out))
    assert any(n.endswith("ARCHIVE_INDEX.md") for n in zipfile.ZipFile(out).namelist())
