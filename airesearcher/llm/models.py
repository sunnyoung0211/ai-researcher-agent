"""模型登记与选择（详细设计 1 第 6.1 节）。

三层配置，后面的覆盖前面的：
1. 仓库默认  configs/models.yaml               （示例与默认值，进仓库）
2. 用户配置  ${AIR_HOME}/models.yaml            （每人自己的模型和 Key 变量名，不进仓库）
3. 项目覆盖  project.yaml 的 models             （某个项目临时换模型，如评价实验固定模型）

配置文件的结构：

    models:                      # 给每个模型起一个名字（别名）
      claude-fast: {model: anthropic/claude-haiku-5-5, key_env: ANTHROPIC_API_KEY, max_tokens: 4096}
    tiers:  {fast: claude-fast, strong: claude-strong}      # 全局默认档位
    stages: {idea: {fast: gpt-mini}}                        # 某个阶段单独指定
    roles:  {exp.coder: strong}                             # 某个角色：可写档位名或模型别名
    fallbacks: {claude-fast: [gpt-mini]}                    # 主模型连续失败时改用

选择顺序：角色（项目 > 用户 > 默认）→ 阶段（项目 > 用户 > 默认）→ 全局档位（项目 > 用户 > 默认）。
阶段代码只请求档位（fast / strong）或写角色名，**不写具体模型名**：换模型只改配置，评价实验可复现。

Key 只放在环境变量或 ${AIR_HOME}/.env 中；配置文件里只写变量名（key_env），绝不写 Key 本身。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field

from airesearcher.core.workspace import air_home, repo_root

DEFAULT_TIERS = ("fast", "strong")
LAYER_LABELS = {"project": "项目配置", "user": "用户配置", "repo": "仓库默认"}

# 各供应商的默认接口域名（用于权限守卫；自定义 api_base 时以 api_base 的域名为准）
PROVIDER_HOSTS = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "gemini": "generativelanguage.googleapis.com",
    "deepseek": "api.deepseek.com",
    "openrouter": "openrouter.ai",
    "ollama": "localhost",
    "ollama_chat": "localhost",
}


class UnknownModel(Exception):
    """配置里引用了没有登记的模型名。"""


class MissingAPIKey(Exception):
    """模型需要的 Key 没有设置。"""


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str  # LiteLLM 格式：provider/model，如 anthropic/claude-sonnet-5-5
    key_env: str | None = None  # 存放 Key 的环境变量名；本地模型可以不填
    api_base: str | None = None  # 自定义接口地址（本地模型、代理）
    max_tokens: int = 4096
    temperature: float | None = None  # None = 不发送（较新的 Claude 模型不接受非默认值）
    params: dict[str, Any] = Field(default_factory=dict)  # 原样传给 LiteLLM 的其他参数
    description: str = ""

    @property
    def provider(self) -> str:
        return self.model.split("/", 1)[0] if "/" in self.model else "openai"

    @property
    def host(self) -> str | None:
        if self.api_base:
            return urlparse(self.api_base).hostname
        return PROVIDER_HOSTS.get(self.provider)


class ModelsLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: dict[str, ModelEntry] = Field(default_factory=dict)
    tiers: dict[str, str] = Field(default_factory=dict)
    stages: dict[str, dict[str, str]] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)


@dataclass
class ResolvedModel:
    alias: str  # 别名；直接写 provider/model 时就是这个字符串
    entry: ModelEntry
    tier: str
    via: str  # 给人看的说明：如“阶段 idea 的 fast（用户配置）”


def repo_config_path() -> Path:
    return repo_root() / "configs" / "models.yaml"


def user_config_path() -> Path:
    return air_home() / "models.yaml"


def dotenv_path() -> Path:
    return air_home() / ".env"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _project_layer(raw: dict | None) -> ModelsLayer:
    """project.yaml 的 models。兼容旧写法 {fast: xxx, strong: yyy}（当作 tiers）。"""
    raw = dict(raw or {})
    structured = {"models", "tiers", "stages", "roles", "fallbacks"}
    if raw and not (set(raw) & structured):
        raw = {"tiers": raw}
    return ModelsLayer.model_validate(raw)


def read_dotenv(path: Path | None = None) -> dict[str, str]:
    """读取 KEY=VALUE 格式的 .env 文件（支持注释、export 前缀和引号）。"""
    path = path or dotenv_path()
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.removeprefix("export ").partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        if k.strip() and v:
            out[k.strip()] = v
    return out


def get_secret(name: str) -> str | None:
    """先看环境变量，再看 ${AIR_HOME}/.env。只在真正调用模型时读取，不写进任何日志。"""
    return os.environ.get(name) or read_dotenv().get(name)


class ModelRegistry:
    """每次创建都重新读配置文件：用户改了 models.yaml 不用重启后台。"""

    def __init__(self, project_models: dict | None = None):
        self.layers: list[tuple[str, ModelsLayer]] = [
            ("project", _project_layer(project_models)),
            ("user", ModelsLayer.model_validate(_load_yaml(user_config_path()))),
            ("repo", ModelsLayer.model_validate(_load_yaml(repo_config_path()))),
        ]
        self.models: dict[str, ModelEntry] = {}
        for _, layer in reversed(self.layers):  # 默认 → 用户 → 项目，后者覆盖前者
            self.models.update(layer.models)

    # ------------------------------------------------------------------ 查找
    @property
    def tier_names(self) -> set[str]:
        names = set(DEFAULT_TIERS)
        for _, layer in self.layers:
            names |= set(layer.tiers)
            for m in layer.stages.values():
                names |= set(m)
        return names

    def entry(self, name: str) -> tuple[str, ModelEntry]:
        if name in self.models:
            return name, self.models[name]
        if "/" in name:  # 直接写 provider/model 也可以（Key 用 LiteLLM 的默认环境变量）
            return name, ModelEntry(model=name)
        raise UnknownModel(
            f"模型 {name!r} 没有登记。请在 {user_config_path()} 的 models 中添加，或运行 air models 查看已登记的模型"
        )

    def resolve(self, role: str = "", stage: str = "", tier: str = "fast") -> ResolvedModel:
        tier = tier or "fast"
        for layer_name, layer in self.layers:
            value = layer.roles.get(role) if role else None
            if not value:
                continue
            if value in self.tier_names:  # 角色指定的是档位
                tier = value
                break
            alias, entry = self.entry(value)
            return ResolvedModel(alias, entry, tier, f"角色 {role}（{LAYER_LABELS[layer_name]}）")
        for layer_name, layer in self.layers:
            value = layer.stages.get(stage, {}).get(tier) if stage else None
            if value:
                alias, entry = self.entry(value)
                return ResolvedModel(alias, entry, tier, f"阶段 {stage} 的 {tier}（{LAYER_LABELS[layer_name]}）")
        for layer_name, layer in self.layers:
            value = layer.tiers.get(tier)
            if value:
                alias, entry = self.entry(value)
                return ResolvedModel(alias, entry, tier, f"全局档位 {tier}（{LAYER_LABELS[layer_name]}）")
        raise UnknownModel(f"档位 {tier!r} 没有配置模型（在 models.yaml 的 tiers 中设置）")

    def fallbacks(self, alias: str) -> list[ResolvedModel]:
        for _, layer in self.layers:
            if alias in layer.fallbacks:
                out = []
                for name in layer.fallbacks[alias]:
                    a, e = self.entry(name)
                    out.append(ResolvedModel(a, e, "", f"{alias} 的备用模型"))
                return out
        return []

    # ------------------------------------------------------------------ 给 CLI / GUI 看
    def key_status(self, entry: ModelEntry) -> bool | None:
        """True = Key 已设置；False = 缺 Key；None = 不需要 Key。"""
        if not entry.key_env:
            return None
        return bool(get_secret(entry.key_env))

    def summary(self, stages: list[str]) -> dict:
        models = []
        for alias, e in sorted(self.models.items()):
            models.append({"alias": alias, "model": e.model, "provider": e.provider, "key_env": e.key_env,
                           "key_set": self.key_status(e), "api_base": e.api_base, "host": e.host,
                           "max_tokens": e.max_tokens, "description": e.description})
        assignments: dict[str, dict[str, Any]] = {}
        for st in stages:
            assignments[st] = {}
            for t in sorted(self.tier_names):
                try:
                    r = self.resolve(stage=st, tier=t)
                    assignments[st][t] = {"alias": r.alias, "model": r.entry.model, "via": r.via}
                except UnknownModel as e:
                    assignments[st][t] = {"alias": None, "model": None, "via": str(e)}
        roles = {}
        for _, layer in reversed(self.layers):
            roles.update(layer.roles)
        warnings = []
        for alias, e in self.models.items():
            if self.key_status(e) is False:
                warnings.append(f"模型 {alias} 需要环境变量 {e.key_env}，但还没有设置")
        return {"models": models, "assignments": assignments, "roles": roles, "warnings": warnings,
                "config_files": {"repo": str(repo_config_path()), "user": str(user_config_path()),
                                 "dotenv": str(dotenv_path())}}


USER_TEMPLATE = """\
# 我的模型配置（只在本机生效，不会提交到仓库）。说明见 configs/models.yaml 和 README 第 9 节。
# 这里只写 Key 所在的环境变量名（key_env），Key 本身写在同目录的 .env 文件里。

models:
  claude-fast:   {model: anthropic/claude-haiku-5-5,  key_env: ANTHROPIC_API_KEY, max_tokens: 4096}
  claude-strong: {model: anthropic/claude-sonnet-5-5, key_env: ANTHROPIC_API_KEY, max_tokens: 8192}
  # gpt-mini:    {model: openai/gpt-5-mini, key_env: OPENAI_API_KEY, max_tokens: 4096}
  # local-qwen:  {model: ollama/qwen2.5, api_base: "http://localhost:11434"}   # 本地模型，不需要 Key

tiers:                 # 全局默认：fast = 大批量便宜调用，strong = 写作、编码、核验
  fast: claude-fast
  strong: claude-strong

stages: {}             # 例：{idea: {fast: gpt-mini}} 让文献阶段的 fast 用 gpt-mini
roles: {}              # 例：{exp.coder: claude-strong}
fallbacks: {}          # 例：{claude-fast: [gpt-mini]}
"""

DOTENV_TEMPLATE = """\
# API Key 写在这里（只在本机，不要提交到 git，不要发给别人）。去掉行首的 # 并填上自己的 Key：
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
"""
