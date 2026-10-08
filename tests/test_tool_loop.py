"""tool_loop()：工具调用循环（详细设计 1 第 6.5 节），用 FakeLLM 模拟模型的工具调用。"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from airesearcher.llm.gateway import ToolLoopExhausted, tool, tool_spec
from airesearcher.testing.fake_llm import FakeToolCalls, fake_gateway

PROMPT = "example/summarize"
VARS = {"goal": "写一个文件", "note": ""}


class Report(BaseModel):
    summary: str
    files: list[str]


def make_tools(project):
    @tool(action="write", target="path")
    def write_file(path: str, content: str) -> str:
        """把内容写入项目内的文件。"""
        p = project.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {path}"

    def read_big() -> str:
        """返回一段很长的文本。"""
        return "x" * 20000

    return [write_file, read_big]


def test_tool_spec_from_signature(project):
    spec = tool_spec(make_tools(project)[0])["function"]
    assert spec["name"] == "write_file" and spec["description"].startswith("把内容写入")
    assert spec["parameters"]["required"] == ["path", "content"]


def test_loop_runs_tools_and_finishes(project):
    llm = fake_gateway(project, {PROMPT: [
        FakeToolCalls([("write_file", {"path": "src/a.py", "content": "print(1)"}), ("read_big", {})]),
        FakeToolCalls([("write_file", {"path": "project.yaml", "content": "evil"})]),  # 权限拒绝
        "我已经写好了。",  # 只用文字回答：网关提醒它调用 finish
        FakeToolCalls([("finish", {"summary": "缺字段"})]),  # finish 参数不合格
        FakeToolCalls([("finish", {"summary": "完成", "files": ["src/a.py"]})]),
    ]})
    res = llm.tool_loop(role="exp.coder", prompt_id=PROMPT, variables=VARS, tools=make_tools(project),
                        finish_schema=Report)
    assert isinstance(res.final, Report) and res.final.files == ["src/a.py"]
    assert (project.root / "src/a.py").read_text(encoding="utf-8") == "print(1)"
    assert "evil" not in (project.root / "project.yaml").read_text(encoding="utf-8")
    assert res.turns == 5 and len(res.call_ids) == 5
    tools = [t.get("tool") for t in res.transcript]
    assert tools[:3] == ["write_file", "read_big", "write_file"]
    denied = res.transcript[2]
    assert denied["error"] and "PermissionDenied" in denied["result"]
    backend = llm.backend
    last_msgs = backend.calls[-1].messages
    big = next(m for m in last_msgs if m["role"] == "tool" and "截断" in m["content"])
    assert len(big["content"]) < 8200
    assert any(m["role"] == "user" and "finish" in m["content"] for m in last_msgs)
    assert backend.calls[0].tools[-1]["function"]["name"] == "finish"
    assert project.budget.used()["llm_calls"] == 5


def test_loop_exhausted(project):
    llm = fake_gateway(project, {PROMPT: [FakeToolCalls([("read_big", {})])]})
    with pytest.raises(ToolLoopExhausted):
        llm.tool_loop(role="r", prompt_id=PROMPT, variables=VARS, tools=make_tools(project), max_turns=3)


def test_unknown_tool_and_bad_json(project):
    llm = fake_gateway(project, {PROMPT: [
        FakeToolCalls([("nope", {}), ("write_file", "{not json")]),
        FakeToolCalls([("finish", {"summary": "ok"})]),
    ]})
    res = llm.tool_loop(role="r", prompt_id=PROMPT, variables=VARS, tools=make_tools(project))
    assert res.final == {"summary": "ok"}
    assert "没有名为 nope" in res.transcript[0]["result"] and "JSON" in res.transcript[1]["result"]
