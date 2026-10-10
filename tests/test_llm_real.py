"""真实模型测试（默认跳过，CI 中不运行）。会产生少量费用（每个模型约几美分）。

运行方法（先在 ~/air/.env 或环境变量中设置对应的 Key）：
    Mac：     AIR_REAL_LLM=1 pytest -q tests/test_llm_real.py
    Windows： set AIR_REAL_LLM=1 然后 pytest -q tests/test_llm_real.py
测哪些模型由 AIR_REAL_MODELS 决定（逗号分隔的模型名字，默认 claude-fast,gpt-mini）；缺 Key 的模型自动跳过。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from airesearcher.llm.gateway import LLMGateway, ping_model
from airesearcher.llm.models import ModelRegistry, read_dotenv
from airesearcher.llm.prompts import STAGES_DIR, PromptLoader
from airesearcher.stages.example.stage import Summary

pytestmark = pytest.mark.skipif(os.environ.get("AIR_REAL_LLM") != "1",
                                reason="设置 AIR_REAL_LLM=1 才运行真实模型测试（会产生少量费用）")
ALIASES = [a.strip() for a in os.environ.get("AIR_REAL_MODELS", "claude-fast,gpt-mini").split(",") if a.strip()]
REAL_HOME = Path(os.environ.get("AIR_HOME", Path.home() / "air")).expanduser()  # conftest 会改 AIR_HOME，先记下


class Answer(BaseModel):
    answer: int


@pytest.fixture(autouse=True)
def real_keys(monkeypatch, air_home):
    """测试用临时 AIR_HOME；把真实 ~/air 里的 .env 和 models.yaml 带过来。"""
    air_home.mkdir(parents=True, exist_ok=True)
    for name in (".env", "models.yaml"):
        if (REAL_HOME / name).exists():
            (air_home / name).write_bytes((REAL_HOME / name).read_bytes())
    for k, v in read_dotenv(air_home / ".env").items():
        monkeypatch.setenv(k, v)


def _need(alias):
    reg = ModelRegistry()
    try:
        _, entry = reg.entry(alias)
    except Exception as e:
        pytest.skip(str(e))
    if reg.key_status(entry) is False:
        pytest.skip(f"{alias} 缺少 {entry.key_env}")


@pytest.mark.parametrize("alias", ALIASES)
def test_ping(alias):
    _need(alias)
    r = ping_model(alias)
    assert r["ok"], r


@pytest.mark.parametrize("alias", ALIASES)
def test_complete_with_schema(project, alias):
    _need(alias)
    project.config.models = {"tiers": {"fast": alias, "strong": alias}}
    resp = LLMGateway(project).complete(role="example.writer", prompt_id="example/summarize",
                                        variables={"goal": "比较两种文本分类方法在小数据上的表现", "note": ""},
                                        response_schema=Summary)
    assert resp.parsed.title and resp.usage["output_tokens"] > 0


@pytest.mark.parametrize("alias", ALIASES)
def test_tool_loop_multi_turn(project, alias):
    """多轮工具调用：检查工具调用格式与思考块的回传（Claude 要求原样带回）。"""
    _need(alias)
    project.config.models = {"tiers": {"fast": alias, "strong": alias}}
    calls = []

    def add(a: int, b: int) -> int:
        """返回 a + b。"""
        calls.append((a, b))
        return a + b

    llm = LLMGateway(project, prompts=PromptLoader([STAGES_DIR, Path(__file__).parent / "prompts"]))
    res = llm.tool_loop(role="testing.calc", prompt_id="testing/calc", variables={"a": 1234, "b": 4321},
                        tools=[add], finish_schema=Answer, max_turns=6)
    assert res.final.answer == 5555 and calls
