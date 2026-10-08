"""air 命令行（详细设计 1 第 8.2 节）。

除 air serve 和 air dev * 外，所有命令都通过 HTTP 调用后台（与 GUI 行为一致，FR-72）。
后台地址默认 http://127.0.0.1:8765，可用环境变量 AIR_SERVER 修改。
大多数命令的项目编号 <pid> 可以省略：省略时使用最近创建的项目。
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from airesearcher.core.models.frontmatter import split_front_matter

from . import labels as L

app = typer.Typer(help="AI Researcher Agent 命令行。先运行 air serve 启动后台，再在另一个终端使用其他命令。",
                  no_args_is_help=True, add_completion=False, rich_markup_mode="rich")
dev_app = typer.Typer(help="开发调试命令（不经过后台，直接操作工作区）。", no_args_is_help=True)
app.add_typer(dev_app, name="dev")
console = Console()

SERVER = os.environ.get("AIR_SERVER", "http://127.0.0.1:8765")
_client: Any = None  # 测试时替换为 FastAPI TestClient


# ====================================================================== HTTP
def client() -> Any:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=SERVER, timeout=60)
    return _client


def fail(msg: str) -> None:
    console.print(f"[bold red]✗ {msg}[/]")
    raise typer.Exit(1)


def api(method: str, path: str, **kw: Any) -> Any:
    try:
        r = client().request(method, path, **kw)
    except httpx.ConnectError:
        fail(f"连不上后台（{SERVER}）。请先在另一个终端运行：air serve")
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {"message": r.text}
        fail(f"{err.get('message', r.text)}（{err.get('code', r.status_code)}）")
    return r.json()


def rid() -> str:
    return str(uuid.uuid4())


def resolve_pid(pid: str | None) -> str:
    if pid:
        return pid
    projects = api("GET", "/api/projects")
    if not projects:
        fail('还没有项目。先运行：air new --idea "你的研究想法"')
    console.print(f"[dim]（未指定项目，使用最近创建的 {projects[0]['project_id']}）[/]")
    return projects[0]["project_id"]


def resolve_approval(aid: str, pid: str | None) -> str:
    """审批编号只在项目内唯一：没给 -p 时，找有这个待审批的项目。"""
    if pid:
        return pid
    projects = api("GET", "/api/projects")
    hits = [p["project_id"] for p in projects
            if any(a["approval_id"] == aid for a in api("GET", f"/api/projects/{p['project_id']}/approvals",
                                                         params={"status": "pending"}))]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        fail(f"多个项目都有待审批 {aid}，请用 -p 指定项目：{', '.join(hits)}")
    return resolve_pid(None)


# ====================================================================== 显示
def _state(s: str) -> str:
    color = L.STATE_COLOR.get(s, "cyan")
    return f"[{color}]{L.label(L.STATE, s)}[/]（{s}）"


def _budget_line(b: dict) -> str:
    parts = []
    for name, c in b["categories"].items():
        if c["hard"] is None and c["used"] == 0:
            continue
        lim = f"/{c['hard']:g}" if c["hard"] is not None else ""
        warn = {"warn": " [yellow]⚠[/]", "exhausted": " [red]✗[/]"}.get(c["level"], "")
        parts.append(f"{L.label(L.BUDGET, name)} {_num(c['used'])}{lim}{warn}")
    return " · ".join(parts)


def _num(x: float) -> str:
    if x == 0:
        return "0"
    if abs(x) < 0.01:
        return "<0.01"
    return f"{x:.2f}".rstrip("0").rstrip(".")


def print_next(st: dict) -> None:
    cmds: list[tuple[str, str]] = []
    for a in st["pending_approvals"]:
        aid = a["approval_id"]
        cmds += [(f"air show {aid}", "查看待审内容"), (f"air approve {aid}", "批准"),
                 (f'air revise {aid} -m "修改意见"', "退回修改"), (f'air reject {aid} -m "理由"', "拒绝")]
    q = st["pending_question"]
    if q:
        cmds.append(("air question", "查看问题和选项"))
        if q["options"]:
            for o in q["options"]:
                cmds.append((f"air answer {o['id']}", o["label"]))
        else:
            cmds.append(('air answer -m "你的回答"', "文字回答"))
    elif "resume" in st["next_actions"]:
        cmds.append(("air resume", "继续项目"))
    if st["state"] in ("IdeaDrafting", "PlanDrafting", "Executing", "Analyzing", "Writing") and not cmds:
        cmds.append(("air status --wait", "等待，直到下一步需要你处理"))
        if st["state"] == "Executing":
            cmds.append(("air runs", "查看运行"))
    if st["state"] == "Completed":
        console.print(f"[green]🎉 项目已完成。论文 PDF：{st['root']}/paper/build/main.pdf[/]")
        cmds.append(("air reopen", "重新打开项目修改论文"))
    if cmds:
        console.print("[bold]接下来可以输入：[/]")
        w = max(len(c) for c, _ in cmds)
        for c, d in cmds:
            console.print(f"  [bold cyan]{escape(c.ljust(w))}[/]   {escape(d)}")


def print_status(st: dict, next_steps: bool = True) -> None:
    console.rule(f"[bold]项目 {st['project_id']}：{st['title']}")
    console.print(f"状态：{_state(st['state'])}")
    prog = st.get("progress") or {}
    ptxt = f"   进度：{prog['done']}/{prog['total']} {prog.get('unit', '')}" if prog.get("total") else ""
    console.print(f"阶段：{st['stage_label']}{ptxt}")
    if st["blocking_reason"]:
        console.print(f"[bold yellow]👉 需要你处理：{escape(st['blocking_reason'])}[/]")
    for a in st["pending_approvals"]:
        t = a["target"]
        console.print(f"  待审批 [bold]{a['approval_id']}[/]（{L.label(L.KIND, a['kind'])}）"
                      f"{escape(t['artifact_id'])} v{t['version']}")
        console.print(f"    [dim]{escape(a['summary'])}[/]")
    q = st["pending_question"]
    if q:
        console.print(f"  待回答问题 [bold]{q['question_id']}[/]：{escape(q['text'].strip().splitlines()[0])}")
    if st["active_runs"]:
        console.print(f"运行中：{len(st['active_runs'])} 个  " + "，".join(
            f"{r['task_key']}（{L.label(L.RUN, r['state'])}）" for r in st["active_runs"][:4]))
    console.print(f"[dim]预算：{_budget_line(st['budget'])}[/]")
    if next_steps:
        print_next(st)


def print_question(q: dict) -> None:
    console.rule(f"[bold]问题 {q['question_id']}（来自 {q['stage']}）")
    console.print(Markdown(q["text"]))
    if q["options"]:
        console.print("[bold]选项：[/]")
        for o in q["options"]:
            d = "（默认）" if o.get("default") else ""
            console.print(f"  [bold cyan]air answer {o['id']}[/]   {escape(o['label'])}{d}")
        if q["allow_text"]:
            console.print('  可以加 -m "补充说明"')
    else:
        console.print('请用文字回答：[bold cyan]air answer -m "你的回答"[/]')


# ====================================================================== 命令
@app.command()
def serve(port: int = typer.Option(8765, help="端口"), host: str = typer.Option("127.0.0.1", help="只监听本机")):
    """启动后台（流程引擎 + API）。保持这个终端开着，在另一个终端输入其他命令。"""
    import uvicorn

    from airesearcher.core.workspace import air_home
    from airesearcher.server.app import create_app

    console.print(f"[bold green]AI Researcher 后台启动：http://{host}:{port}[/]   接口说明：http://{host}:{port}/docs")
    console.print(f"[dim]项目目录：{air_home()}/projects   按 Ctrl+C 停止[/]")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@app.command()
def new(
    idea: str = typer.Option(..., "--idea", help="研究想法（一两句话）"),
    task: str = typer.Option("tasks/smoke", "--task", help="任务配置目录"),
    title: str = typer.Option(None, "--title", help="项目标题（默认取想法的第一句）"),
    no_evidence_check: bool = typer.Option(False, "--no-evidence-check", help="关闭论断-证据检查（对照实验用）"),
    impl: str = typer.Option(None, "--impl", help='阶段实现，如 "fake" 或 "idea=real,plan=fake"（默认全部 fake）'),
    smoke_seconds: float = typer.Option(None, "--smoke-seconds", help="假实验：每个运行跑几秒"),
    smoke_fail: list[str] = typer.Option(None, "--smoke-fail", help="假实验：让某个运行失败，如 E2-seed=1:exit1"),
):
    """创建项目并开始。"""
    dev: dict[str, Any] = {}
    if impl:
        dev["stage_impl"] = _parse_impl(impl)
    if smoke_seconds is not None:
        dev["smoke_seconds"] = smoke_seconds
    if smoke_fail:
        dev["smoke_fail"] = dict(x.rsplit(":", 1) for x in smoke_fail)
    body = {"title": title, "idea_text": idea, "task": task, "evidence_check": not no_evidence_check,
            "request_id": rid(), "dev": dev or None}
    p = api("POST", "/api/projects", json=body)
    console.print(f"[bold green]✓ 已创建项目 {p['project_id']}[/]  目录：{p['root']}")
    time.sleep(1.5)
    print_status(api("GET", f"/api/projects/{p['project_id']}"))


def _parse_impl(s: str) -> dict[str, str]:
    if "=" not in s:
        return {name: s for name in ("idea", "plan", "experiment", "writing")}
    return {k.strip(): v.strip() for k, v in (x.split("=", 1) for x in s.split(","))}


@app.command("list")
def list_projects():
    """列出所有项目。"""
    rows = api("GET", "/api/projects")
    if not rows:
        console.print('还没有项目。运行：air new --idea "你的研究想法"')
        return
    t = Table("项目编号", "标题", "状态", "待审批", "问题", "创建时间")
    for r in rows:
        t.add_row(r["project_id"], r["title"], L.label(L.STATE, r["state"]), str(r["pending_approvals"]),
                  "有" if r["has_question"] else "", r["created_at"][:16].replace("T", " "))
    console.print(t)


@app.command()
def status(
    pid: str = typer.Argument(None, help="项目编号（省略 = 最近的项目）"),
    wait: bool = typer.Option(False, "--wait", "-w", help="一直等到需要你处理（审批/问题）或项目结束"),
    timeout: int = typer.Option(900, help="--wait 最多等多少秒"),
):
    """查看项目当前状态、需要你做什么、以及下一步命令。"""
    pid = resolve_pid(pid)
    st = api("GET", f"/api/projects/{pid}")
    if wait:
        t0, last = time.time(), None
        while time.time() - t0 < timeout:
            if st["blocking_reason"] or st["state"] in ("Completed", "Failed", "Paused", "BudgetExhausted"):
                break
            prog = st.get("progress") or {}
            line = f"{L.label(L.STATE, st['state'])} · {st['stage_label']}"
            if prog.get("total"):
                line += f" · {prog['done']}/{prog['total']}"
            if line != last:
                console.print(f"[dim]{time.strftime('%H:%M:%S')}[/] {line}")
                last = line
            time.sleep(2)
            st = api("GET", f"/api/projects/{pid}")
    print_status(st)


@app.command()
def approvals(pid: str = typer.Argument(None, help="项目编号"),
              all: bool = typer.Option(False, "--all", help="包括已处理的")):
    """列出待审批（--all 显示全部）。"""
    pid = resolve_pid(pid)
    rows = api("GET", f"/api/projects/{pid}/approvals", params=None if all else {"status": "pending"})
    if not rows:
        console.print("没有待审批。")
        return
    t = Table("编号", "类别", "产物", "状态", "摘要")
    for a in rows:
        target = f"{a['target']['artifact_id']} v{a['target']['version']}"
        t.add_row(a["approval_id"], L.label(L.KIND, a["kind"]), escape(target), L.label(L.APPROVAL, a["status"]),
                  escape(a["summary"][:60]))
    console.print(t)


@app.command()
def show(aid: str = typer.Argument(..., help="审批编号，如 ap-0001"), pid: str = typer.Option(None, "-p", "--project")):
    """查看待审内容（终端里显示 Markdown）。"""
    pid = resolve_approval(aid, pid)
    d = api("GET", f"/api/projects/{pid}/approvals/{aid}")
    a = d["approval"]
    t = a["target"]
    console.rule(f"[bold]审批 {aid}：{L.label(L.KIND, a['kind'])}  {t['artifact_id']} v{t['version']}")
    console.print(f"状态：{L.label(L.APPROVAL, a['status'])}")
    console.print(f"摘要：{escape(a['summary'])}")
    console.print(f"影响：{escape(a['impact'])}")
    content = d["target_content"]
    if content and t["artifact_id"].endswith(".md"):
        try:
            _, body = split_front_matter(content)
            console.print(f"[dim]（文件开头的 YAML 数据区已省略，完整文件：{d['target_path']}）[/]")
        except ValueError:
            body = content
        console.print(Markdown(body))
    elif t["artifact_id"] == "paper":
        console.print(f"论文目录：{d['target_path']}")
        console.print(f"[bold]PDF：{d['target_path']}/build/main.pdf[/]（用 PDF 阅读器打开）")
        info = api("GET", f"/api/projects/{pid}/paper")
        if info.get("review_summary"):
            md = info["review_summary"]["file"].removesuffix(".json") + ".md"
            review = api("GET", f"/api/projects/{pid}/files", params={"path": md})
            console.print(Markdown(review["content"]))
        console.print("[dim]（以下附件为原始数据，供需要时查看）[/]")
    elif content:
        console.print(content[:4000])
    for e in d["extra"]:
        console.print(f"\n[bold]附：{escape(e['artifact_id'])} v{e['version']}[/]")
        if e["content"] and e["artifact_id"].endswith(".md"):
            console.print(Markdown(e["content"]))
        elif e["content"] and t["artifact_id"] != "paper":
            console.print(escape("\n".join(e["content"].splitlines()[:25])), style="dim")
    console.print()
    if a["status"] == "pending":
        console.print(f"批准：[bold cyan]air approve {aid}[/]   退回：[bold cyan]air revise {aid} -m \"意见\"[/]"
                      f"   拒绝：[bold cyan]air reject {aid} -m \"理由\"[/]")


def _decide(aid: str, decision: str, comment: str, pid: str | None) -> None:
    pid = resolve_approval(aid, pid)
    d = api("GET", f"/api/projects/{pid}/approvals/{aid}")
    body = {"decision": decision, "comment": comment or "", "expected_sha256": d["approval"]["target"]["sha256"],
            "request_id": rid()}
    a = api("POST", f"/api/projects/{pid}/approvals/{aid}/decision", json=body)
    console.print(f"[bold green]✓ 审批 {aid}：{L.label(L.APPROVAL, a['status'])}[/]")
    time.sleep(0.5)
    print_status(api("GET", f"/api/projects/{pid}"))


@app.command()
def approve(aid: str = typer.Argument(..., help="审批编号"), m: str = typer.Option("", "-m", help="意见（可选）"),
            pid: str = typer.Option(None, "-p", "--project")):
    """批准。"""
    _decide(aid, "approved", m, pid)


@app.command()
def revise(aid: str = typer.Argument(..., help="审批编号"), m: str = typer.Option(..., "-m", help="修改意见"),
           pid: str = typer.Option(None, "-p", "--project")):
    """退回修改（必须写意见）。"""
    _decide(aid, "changes_requested", m, pid)


@app.command()
def reject(aid: str = typer.Argument(..., help="审批编号"), m: str = typer.Option(..., "-m", help="拒绝理由"),
           pid: str = typer.Option(None, "-p", "--project")):
    """拒绝（之后会问你：重写还是结束项目）。"""
    _decide(aid, "rejected", m, pid)


@app.command()
def question(pid: str = typer.Argument(None, help="项目编号")):
    """查看当前待回答的问题。"""
    pid = resolve_pid(pid)
    q = api("GET", f"/api/projects/{pid}/question")
    if not q:
        console.print("没有待回答的问题。")
        return
    print_question(q)


@app.command()
def answer(args: list[str] = typer.Argument(None, help="[项目编号] 选项，如 air answer skip 或 air answer p-xxx skip"),
           m: str = typer.Option("", "-m", help="文字回答 / 补充说明")):
    """回答当前问题：air answer <选项> [-m 文字]。"""
    args = args or []
    pid, choice = (args[0], args[1]) if len(args) >= 2 else (None, args[0] if args else None)
    pid = resolve_pid(pid)
    q = api("GET", f"/api/projects/{pid}/question")
    if not q:
        fail("没有待回答的问题。")
    st = api("POST", f"/api/projects/{pid}/question/answer",
             json={"question_id": q["question_id"], "choice": choice, "text": m, "request_id": rid()})
    console.print(f"[bold green]✓ 已回答 {q['question_id']}：{choice or m}[/]")
    print_status(st)


def _action(pid: str | None, action: str) -> None:
    pid = resolve_pid(pid)
    st = api("POST", f"/api/projects/{pid}/actions", json={"action": action, "request_id": rid()})
    print_status(st)


@app.command()
def pause(pid: str = typer.Argument(None)):
    """暂停项目（已在跑的实验会继续跑完）。"""
    _action(pid, "pause")


@app.command()
def resume(pid: str = typer.Argument(None)):
    """继续已暂停 / 预算用尽 / 出错的项目。"""
    _action(pid, "resume")


@app.command()
def cancel(pid: str = typer.Argument(None)):
    """取消项目。"""
    _action(pid, "cancel")


@app.command()
def reopen(pid: str = typer.Argument(None)):
    """重新打开已完成的项目，回到撰写论文。"""
    _action(pid, "reopen")


@app.command()
def runs(pid: str = typer.Argument(None)):
    """列出实验运行。"""
    pid = resolve_pid(pid)
    rows = api("GET", f"/api/projects/{pid}/runs")
    if not rows:
        console.print("还没有运行。")
        return
    t = Table("运行编号", "任务", "类型", "状态", "失败原因", "重试自")
    for r in rows:
        t.add_row(r["run_id"], r["task_key"], L.label(L.RUN_KIND, r["kind"]), L.label(L.RUN, r["state"]),
                  L.label(L.FAILURE, r["failure_reason"]), r["retry_of"] or "")
    console.print(t)


@app.command()
def logs(args: list[str] = typer.Argument(..., help="[项目编号] 运行编号"),
         follow: bool = typer.Option(False, "-f", "--follow", help="持续输出直到运行结束"),
         stderr: bool = typer.Option(False, "--stderr", help="看 stderr")):
    """查看运行日志：air logs <run_id> [-f]。"""
    pid, run_id = (args[0], args[1]) if len(args) >= 2 else (None, args[0])
    pid = resolve_pid(pid)
    offset = 0
    while True:
        c = api("GET", f"/api/projects/{pid}/runs/{run_id}/logs",
                params={"stream": "stderr" if stderr else "stdout", "offset": offset})
        if c["text"]:
            console.out(c["text"], end="")
        offset = c["next_offset"]
        if not follow or c["eof"]:
            break
        time.sleep(1)


# ====================================================================== air dev
@dev_app.command("new-workspace")
def dev_new_workspace(
    path: Path = typer.Argument(..., help="新工作区目录"),
    from_: Path = typer.Option(None, "--from", help="从已有项目目录复制（如 fixtures/sample_project）"),
    idea: str = typer.Option("比较基线、主方法和消融在冒烟任务上的得分", "--idea"),
    task: str = typer.Option("tasks/smoke", "--task"),
    impl: str = typer.Option(None, "--impl", help='如 "idea=example" 或 "fake"'),
):
    """创建一个可随便改的测试工作区（不登记到后台）。"""
    from airesearcher.core.project import Project

    if path.exists() and any(path.iterdir()):
        fail(f"{path} 已存在且不为空")
    if from_:
        shutil.copytree(from_, path, dirs_exist_ok=True)
        console.print(f"[green]✓ 已从 {from_} 复制到 {path}[/]")
        return
    dev = {"stage_impl": _parse_impl(impl)} if impl else {}
    p = Project.create(goal=idea, task=task, root=path, register=False, dev=dev)
    console.print(f"[green]✓ 已创建工作区 {p.root}（项目 {p.project_id}）[/]")


@dev_app.command("run-stage")
def dev_run_stage(
    stage: str = typer.Argument(..., help="阶段名：idea / plan / experiment / writing / example"),
    workspace: Path = typer.Option(..., "--workspace", "-w", help="工作区目录"),
    steps: int = typer.Option(5, "--steps", help="最多执行几步"),
    state: str = typer.Option(None, "--state", help="先把项目状态设为这个值（如 PlanDrafting）"),
    auto_approve: bool = typer.Option(False, "--auto-approve", help="遇到审批自动批准"),
    fake_llm: bool = typer.Option(False, "--fake-llm", help="用 FakeLLM（不需要 API Key；示例阶段可用）"),
):
    """不启动后台，直接在工作区上执行阶段的 step()，打印每步的 StepResult。"""
    from airesearcher.core.locks import ProjectLock
    from airesearcher.core.models.common import ProjectState
    from airesearcher.core.project import Project
    from airesearcher.engine.engine import ProjectEngine
    from airesearcher.engine.stage import Wait, describe
    from airesearcher.testing.fake_llm import fake_gateway

    project = Project.open(workspace)
    llm = fake_gateway(project, default={"title": "（FakeLLM）示例标题", "points": ["要点一", "要点二"]}) \
        if fake_llm else None
    with ProjectLock(project.root):
        eng = ProjectEngine(project, llm=llm, retry_delay=0)
        eng.stage_override = stage
        if state:
            eng.ck.state = ProjectState(state)
            eng._save()
        for i in range(steps):
            if auto_approve and eng.auto_approve_pending():
                console.print("[dim]  （已自动批准待审批）[/]")
            before = eng.state.value
            t = eng.tick()
            after = eng.state.value
            if t.result is not None:
                console.print(f"[bold]{i + 1}.[/] [{before}] {describe(t.result)}"
                              + (f"  → {after}" if after != before else ""))
                if isinstance(t.result, Wait):
                    time.sleep(min(t.result.seconds, 5))
            elif after != before:
                why = f"（{escape(eng.ck.reason)}）" if eng.ck.reason else ""
                console.print(f"[bold]{i + 1}.[/] {before} → {after}{why}")
            else:
                console.print(f"[yellow]项目处于等待状态 {after}"
                              + (f"（{eng.ck.reason}）" if eng.ck.reason else "")
                              + "，没有可执行的 step（遇到审批可加 --auto-approve）。[/]")
                break


if __name__ == "__main__":
    app()
