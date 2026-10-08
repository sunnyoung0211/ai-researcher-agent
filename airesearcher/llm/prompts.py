"""提示文件加载与渲染（详细设计 1 第 6.2 节）。

每个提示一个文件：airesearcher/stages/<stage>/prompts/<name>.v<N>.md
修改提示 = 新建 v<N+1> 文件，旧文件保留，这样每次调用记录的 prompt_id + version 能对应到确切文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import jinja2

from airesearcher.core.models.frontmatter import split_front_matter

STAGES_DIR = Path(__file__).resolve().parents[1] / "stages"
_BLOCK = re.compile(r"<(system|user)>\s*(.*?)\s*</\1>", re.S)
_FILE = re.compile(r"^(?P<name>.+)\.v(?P<ver>\d+)\.md$")


class PromptNotFound(Exception):
    pass


@dataclass
class Prompt:
    id: str
    version: int
    tier: str | None
    description: str
    output_schema: str | None
    system: str
    user: str
    path: Path
    meta: dict = field(default_factory=dict)

    def render(self, variables: dict) -> tuple[str, str]:
        """{{ }} 中的变量由调用方提供，缺失时报错而不是留空。"""
        env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False, keep_trailing_newline=True)
        try:
            return env.from_string(self.system).render(**variables), env.from_string(self.user).render(**variables)
        except jinja2.UndefinedError as e:
            raise ValueError(f"提示 {self.id}.v{self.version} 缺少变量：{e.message}") from e


class PromptLoader:
    def __init__(self, roots: list[Path] | None = None):
        self.roots = roots or [STAGES_DIR]

    def _candidates(self, prompt_id: str) -> list[tuple[int, Path]]:
        stage, _, name = prompt_id.partition("/")
        if not name:
            raise PromptNotFound(f"prompt_id 应形如 <stage>/<name>：{prompt_id}")
        out = []
        for root in self.roots:
            d = root / stage / "prompts"
            for p in d.glob(f"{name}.v*.md") if d.exists() else []:
                m = _FILE.match(p.name)
                if m and m.group("name") == name:
                    out.append((int(m.group("ver")), p))
        return sorted(out)

    def versions(self, prompt_id: str) -> list[int]:
        return [v for v, _ in self._candidates(prompt_id)]

    def load(self, prompt_id: str, version: int | None = None) -> Prompt:
        cands = self._candidates(prompt_id)
        if not cands:
            raise PromptNotFound(f"找不到提示文件 {prompt_id}")
        if version is None:
            ver, path = cands[-1]  # 省略时取最大版本号
        else:
            match = [c for c in cands if c[0] == version]
            if not match:
                raise PromptNotFound(f"找不到提示 {prompt_id} 的 v{version}")
            ver, path = match[0]
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        blocks = dict(_BLOCK.findall(body))
        if "user" not in blocks:
            raise ValueError(f"{path} 缺少 <user> 区块")
        return Prompt(
            id=meta.get("id", prompt_id), version=int(meta.get("version", ver)), tier=meta.get("tier"),
            description=meta.get("description", ""), output_schema=meta.get("output_schema"),
            system=blocks.get("system", ""), user=blocks["user"], path=path, meta=meta,
        )
