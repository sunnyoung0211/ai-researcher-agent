"""Skill 加载（详细设计 1 第 7 节）。最小版本：读取 skill.yaml / SKILL.md，加载 validators.py 中的检查函数。

    sk = ctx.skills.load("scientific-plotting")
    sk.instructions            # SKILL.md 内容
    sk.path("scripts/plot_bar.py")
    sk.validate(output) -> list[ValidationIssue]
    sk.ref                     # "scientific-plotting@1.0.0"

TODO（第 2 周）：skills/registry.yaml 的版本选择、air skills verify。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from airesearcher.core.workspace import repo_root


class ValidationIssue(BaseModel):
    validator: str
    message: str
    severity: str = "error"


class SkillNotFound(Exception):
    pass


class MissingRequirement(Exception):
    pass


@dataclass
class Skill:
    name: str
    version: str
    root: Path
    meta: dict
    instructions: str
    validators: list[Any] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"路径越出了 skill 目录：{rel}")
        return p

    def validate(self, output: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for fn in self.validators:
            try:
                res = fn(output) or []
            except Exception as e:
                res = [f"检查函数出错：{e!r}"]
            for r in res if isinstance(res, list) else [res]:
                if isinstance(r, ValidationIssue):
                    issues.append(r)
                else:
                    issues.append(ValidationIssue(validator=fn.__name__, message=str(r)))
        return issues


_REQ = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(>=)?\s*([0-9.]+)?")


def _check_requirement(req: str) -> None:
    m = _REQ.match(req.strip())
    if not m:
        return
    name, _, minimum = m.groups()
    try:
        have = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        raise MissingRequirement(f"缺少依赖 {name}，请运行：pip install '{req}'") from None
    if minimum:
        def as_tuple(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
        if as_tuple(have) < as_tuple(minimum):
            raise MissingRequirement(f"{name} 版本 {have} 低于要求 {minimum}，请运行：pip install '{req}'")


class SkillLoader:
    def __init__(self, roots: list[Path] | None = None):
        self.roots = roots or [repo_root() / "skills"]

    def available(self) -> list[str]:
        out = []
        for r in self.roots:
            out += [p.parent.name for p in r.glob("*/skill.yaml")] if r.exists() else []
        return sorted(out)

    def load(self, name: str, version: str | None = None) -> Skill:
        for r in self.roots:
            d = r / name
            if (d / "skill.yaml").exists():
                break
        else:
            raise SkillNotFound(f"找不到 skill {name}（在 {', '.join(map(str, self.roots))} 下）")
        meta = yaml.safe_load((d / "skill.yaml").read_text(encoding="utf-8")) or {}
        ver = str(meta.get("version", "0"))
        if version is not None and version != ver:
            raise SkillNotFound(f"skill {name} 当前版本是 {ver}，没有 {version}")
        for req in meta.get("requires") or []:
            _check_requirement(req)
        instructions = (d / "SKILL.md").read_text(encoding="utf-8") if (d / "SKILL.md").exists() else ""
        validators = []
        names = meta.get("validators") or []
        if names and (d / "validators.py").exists():
            spec = importlib.util.spec_from_file_location(f"air_skill_{name.replace('-', '_')}", d / "validators.py")
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for n in names:
                if not hasattr(mod, n):
                    raise SkillNotFound(f"skill {name} 的 validators.py 中没有函数 {n}")
                validators.append(getattr(mod, n))
        return Skill(name=meta.get("name", name), version=ver, root=d, meta=meta, instructions=instructions,
                     validators=validators)
