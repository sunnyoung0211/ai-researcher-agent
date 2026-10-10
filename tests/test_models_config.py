"""模型配置：三层覆盖、选择顺序、Key 读取、权限守卫、请求参数。"""

from __future__ import annotations

import json

import pytest
import yaml

from airesearcher.core.errors import PermissionDenied
from airesearcher.llm.gateway import LLMRequest, litellm_kwargs
from airesearcher.llm.models import MissingAPIKey, ModelRegistry, UnknownModel, read_dotenv
from airesearcher.testing.fake_llm import fake_gateway

USER = {
    "models": {
        "gpt-mini": {"model": "openai/gpt-5-mini", "key_env": "OPENAI_API_KEY"},
        "local-qwen": {"model": "ollama/qwen2.5", "api_base": "http://localhost:11434"},
        "remote-llm": {"model": "openai/x", "api_base": "https://llm.example.org/v1", "key_env": "X_KEY"},
    },
    "tiers": {"fast": "gpt-mini"},
    "stages": {"idea": {"fast": "local-qwen"}, "writing": {"strong": "gpt-mini"}},
    "roles": {"lit.coordinator": "strong", "exp.reviewer": "local-qwen"},
}


@pytest.fixture
def user_config(air_home):
    air_home.mkdir(parents=True, exist_ok=True)
    (air_home / "models.yaml").write_text(yaml.safe_dump(USER), encoding="utf-8")
    return air_home


def test_repo_defaults_only(air_home):
    reg = ModelRegistry()
    r = reg.resolve(role="example.writer", stage="example", tier="fast")
    assert r.alias == "claude-fast" and r.entry.model == "anthropic/claude-haiku-5-5"
    assert r.entry.temperature is None  # 新 Claude 模型不发送 temperature
    assert reg.resolve(role="lit.coordinator", stage="idea", tier="fast").alias == "claude-strong"


def test_resolution_order(user_config):
    reg = ModelRegistry({"stages": {"idea": {"fast": "claude-fast"}}})
    # 全局档位：用户配置覆盖仓库默认
    assert reg.resolve(stage="plan", tier="fast").alias == "gpt-mini"
    # 阶段：项目配置覆盖用户配置
    r = reg.resolve(stage="idea", tier="fast")
    assert r.alias == "claude-fast" and "项目" in r.via
    # 用户配置的阶段设置
    assert reg.resolve(stage="writing", tier="strong").alias == "gpt-mini"
    # 角色写档位名：再按阶段 / 全局档位解析
    assert reg.resolve(role="lit.coordinator", stage="idea", tier="fast").alias == "claude-strong"
    # 角色写模型别名：优先于阶段设置
    r = reg.resolve(role="exp.reviewer", stage="writing", tier="strong")
    assert r.alias == "local-qwen" and "角色" in r.via


def test_legacy_project_models_and_raw_model_string(user_config):
    reg = ModelRegistry({"fast": "anthropic/claude-opus-5-5"})  # 旧写法 {档位: 模型}
    r = reg.resolve(stage="plan", tier="fast")
    assert r.alias == "anthropic/claude-opus-5-5" and r.entry.model == "anthropic/claude-opus-5-5"


def test_unknown_model_is_a_clear_error(user_config):
    with pytest.raises(UnknownModel):
        ModelRegistry({"tiers": {"fast": "no-such-model"}}).resolve(stage="plan", tier="fast")


def test_dotenv_and_key_status(user_config, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (user_config / ".env").write_text('# comment\nexport OPENAI_API_KEY="sk-test-123"\nEMPTY=\n', encoding="utf-8")
    assert read_dotenv() == {"OPENAI_API_KEY": "sk-test-123"}
    summary = ModelRegistry().summary(["idea", "plan"])
    gpt = next(m for m in summary["models"] if m["alias"] == "gpt-mini")
    assert gpt["key_set"] is True
    assert next(m for m in summary["models"] if m["alias"] == "local-qwen")["key_set"] is None
    assert "sk-test-123" not in json.dumps(summary)  # Key 本身绝不出现在给 CLI/GUI 的数据中
    assert summary["assignments"]["idea"]["fast"]["alias"] == "local-qwen"


def test_litellm_kwargs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    req = LLMRequest(role="r", prompt_id="p/x", prompt_version=1, model="anthropic/claude-sonnet-5-5",
                     messages=[], max_tokens=100, key_env="ANTHROPIC_API_KEY", params={"reasoning_effort": "low"})
    kw = litellm_kwargs(req)
    assert "temperature" not in kw and kw["api_key"] == "sk-ant-test" and kw["reasoning_effort"] == "low"
    assert "sk-ant-test" not in req.model_dump_json()  # Key 不进入请求对象（也就不进入缓存和日志）
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(MissingAPIKey):
        litellm_kwargs(req)


def test_network_guard_for_model_hosts(user_config, project):
    """用户在模型配置中登记的接口域名视为已授权；其他域名仍按项目白名单检查。"""
    project.config.models = {"tiers": {"fast": "remote-llm"}}
    llm = fake_gateway(project, {"example/summarize": [{"title": "t", "points": []}]})
    llm.complete(role="r", prompt_id="example/summarize", variables={"goal": "g", "note": ""})
    calls = [json.loads(x) for x in (project.root / ".llm/calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert calls[-1]["alias"] == "remote-llm" and "全局档位" in calls[-1]["via"]
    granted = [e for e in project.events.all() if e.type == "permission.granted"]
    assert any(e.data["target"] == "llm.example.org" for e in granted)
    with pytest.raises(PermissionDenied):  # 未登记的域名（例如 Agent 想访问的网站）仍然拒绝
        project.permissions.guard("network", "evil.example.org", extra_allow=ModelRegistry().hosts())


def test_relay_model_without_provider_prefix(user_config, project):
    """第三方中转站：只写模型名 + api_base → 自动按 OpenAI 兼容接口处理；可配置自己的价格。"""
    cfg = yaml.safe_load((user_config / "models.yaml").read_text(encoding="utf-8"))
    cfg["models"]["relay"] = {"model": "gpt-6.1-sol", "api_base": "https://www.relay.example/v1",
                              "key_env": "RELAY_KEY", "price": [1.0, 2.0]}
    cfg["models"]["broken"] = {"model": "gpt-6.1-sol"}
    (user_config / "models.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reg = ModelRegistry()
    _, relay = reg.entry("relay")
    assert relay.litellm_model == "openai/gpt-6.1-sol" and relay.host == "www.relay.example"
    assert any("broken" in w and "provider/model" in w for w in reg.summary(["idea"])["warnings"])
    project.config.models = {"tiers": {"fast": "relay"}}
    llm = fake_gateway(project, {"example/summarize": [{"title": "t", "points": []}]})
    resp = llm.complete(role="r", prompt_id="example/summarize", variables={"goal": "g", "note": ""})
    assert resp.model == "openai/gpt-6.1-sol"
    assert llm.backend.calls[-1].model == "openai/gpt-6.1-sol"
    req = LLMRequest(role="r", prompt_id="p/x", prompt_version=1, model="gpt-6.1-sol", messages=[], max_tokens=10)
    with pytest.raises(ValueError, match="provider/model"):
        litellm_kwargs(req)
