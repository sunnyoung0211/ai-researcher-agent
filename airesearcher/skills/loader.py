"""Skill 加载（详细设计 1 第 7 节）。最小版本：读取 skill.yaml / SKILL.md，加载 validators.py 中的检查函数。

    sk = ctx.skills.load("scientific-plotting")
    sk.instructions            # SKILL.md 内容
    sk.path("scripts/plot_bar.py")
    sk.validate(output) -> list[ValidationIssue]
    sk.ref                     # "scientific-plotting@1.0.0"

版本与启用（第 7.3 节）：skills/registry.yaml 记录每个 skill 启用的版本。skill.yaml 里的版本号改了之后，
要先运行 `air skills verify <名字>` 用 sample/ 里的小样例验证，通过后才会写进 registry；在此之前 load() 会报错。
开发中想先试用没验证的版本，设置环境变量 AIR_SKILLS_UNVERIFIED=1。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import tempfile
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


class SkillNotVerified(Exception):
    """skill.yaml 的版本还没有通过 air skills verify（不在 registry.yaml 中）。"""


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


REGISTRY_HEADER = ("# 每个 skill 当前启用的版本（详细设计 1 第 7.3 节）。\n"
                   "# 不要手改：改了 skill.yaml 的版本号后运行 air skills verify <名字>，验证通过会自动更新这里。\n")


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SkillLoader:
    def __init__(self, roots: list[Path] | None = None, registry: Path | None = None):
        self.roots = roots or [repo_root() / "skills"]
        self.registry_path = registry or self.roots[0] / "registry.yaml"

    def available(self) -> list[str]:
        out = []
        for r in self.roots:
            out += [p.parent.name for p in r.glob("*/skill.yaml")] if r.exists() else []
        return sorted(out)

    # ---------- registry ----------
    def registry(self) -> dict[str, str]:
        if not self.registry_path.exists():
            return {}
        data = yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {}
        return {str(k): str(v) for k, v in data.items()}

    def _set_enabled(self, name: str, version: str) -> None:
        reg = self.registry()
        reg[name] = version
        body = yaml.safe_dump(dict(sorted(reg.items())), allow_unicode=True, sort_keys=False)
        self.registry_path.write_text(REGISTRY_HEADER + body, encoding="utf-8")

    def _dir(self, name: str) -> Path:
        for r in self.roots:
            if (r / name / "skill.yaml").exists():
                return r / name
        raise SkillNotFound(f"找不到 skill {name}（在 {', '.join(map(str, self.roots))} 下）")

    def meta(self, name: str) -> dict:
        """skill.yaml 的内容（不检查依赖、不看是否已启用）。"""
        return yaml.safe_load((self._dir(name) / "skill.yaml").read_text(encoding="utf-8")) or {}

    def current_version(self, name: str) -> str:
        return str(self.meta(name).get("version", "0"))

    def problems(self) -> list[str]:
        """registry 与 skill 目录对不上的地方（CI 中检查：每个 skill 的当前版本都必须已验证）。"""
        reg, out = self.registry(), []
        for name in self.available():
            cur = self.current_version(name)
            if name not in reg:
                out.append(f"{name}：版本 {cur} 还没有登记，请运行 air skills verify {name}")
            elif reg[name] != cur:
                out.append(f"{name}：skill.yaml 是 {cur}，启用的是 {reg[name]}，请运行 air skills verify {name}")
        for name in sorted(set(reg) - set(self.available())):
            out.append(f"{name}：registry.yaml 中有，但找不到这个 skill 的目录")
        return out

    # ---------- 加载 ----------
    def load(self, name: str, version: str | None = None, allow_unverified: bool = False) -> Skill:
        d = self._dir(name)
        meta = yaml.safe_load((d / "skill.yaml").read_text(encoding="utf-8")) or {}
        ver = str(meta.get("version", "0"))
        if version is not None and version != ver:
            raise SkillNotFound(f"skill {name} 当前版本是 {ver}，没有 {version}")
        allow_unverified = allow_unverified or os.environ.get("AIR_SKILLS_UNVERIFIED") == "1"
        enabled = self.registry().get(name)
        if not allow_unverified and enabled != ver:
            what = f"启用的是 {enabled}" if enabled else "registry.yaml 中还没有登记"
            raise SkillNotVerified(f"skill {name} 的版本 {ver} 还没有通过验证（{what}）。"
                                   f"请运行 air skills verify {name}；开发时可设置 AIR_SKILLS_UNVERIFIED=1 先试用")
        for req in meta.get("requires") or []:
            _check_requirement(req)
        instructions = (d / "SKILL.md").read_text(encoding="utf-8") if (d / "SKILL.md").exists() else ""
        validators = []
        names = meta.get("validators") or []
        if names and (d / "validators.py").exists():
            mod = _load_module(d / "validators.py", f"air_skill_{name.replace('-', '_')}")
            for n in names:
                if not hasattr(mod, n):
                    raise SkillNotFound(f"skill {name} 的 validators.py 中没有函数 {n}")
                validators.append(getattr(mod, n))
        elif names:
            raise SkillNotFound(f"skill {name} 的 skill.yaml 写了 validators，但没有 validators.py")
        return Skill(name=meta.get("name", name), version=ver, root=d, meta=meta, instructions=instructions,
                     validators=validators)

    # ---------- 验证 ----------
    def verify(self, name: str, update_registry: bool = True) -> VerifyReport:
        """用 sample/ 中的小样例验证 skill 的当前版本，通过后（默认）把它写进 registry.yaml。"""
        rep = VerifyReport(name=name)
        try:
            sk = self.load(name, allow_unverified=True)
        except (SkillNotFound, MissingRequirement) as e:
            rep.fail("加载", str(e))
            return rep
        rep.version = sk.version
        rep.ok_step("加载", f"依赖齐全，{len(sk.validators)} 个检查函数")
        sample = sk.root / "sample"
        expected = sample / "expected"
        if not (sample / "input").is_dir() or not expected.is_dir():
            rep.fail("样例", "缺少 sample/input/ 或 sample/expected/（每个 skill 都要带一个小样例）")
            return rep
        with tempfile.TemporaryDirectory(prefix=f"air-skill-{name}-") as tmp:
            out_dir = Path(tmp)
            outputs: list[tuple[str, Any]] = []
            if (sample / "run.py").exists():
                try:
                    mod = _load_module(sample / "run.py", f"air_skill_sample_{name.replace('-', '_')}")
                    result = mod.run(sk, sample / "input", out_dir)
                except Exception as e:
                    rep.fail("运行样例", f"sample/run.py 出错：{type(e).__name__}: {e}")
                    return rep
                rep.ok_step("运行样例", "sample/run.py 已运行")
                diffs = _compare_dirs(expected, out_dir)
                if diffs:
                    rep.fail("比对结果", "；".join(diffs))
                else:
                    rep.ok_step("比对结果", f"与 sample/expected/ 一致（{len(_files(expected))} 个文件）")
                outputs = [("run() 的返回值", result)] if result is not None else _file_outputs(out_dir)
            else:
                rep.ok_step("运行样例", "没有 sample/run.py，只用检查函数检查 sample/expected/")
                outputs = _file_outputs(expected)
            issues = [f"{label}：{i.message}（{i.validator}）" for label, out in outputs for i in sk.validate(out)]
            if issues:
                rep.fail("检查函数", "；".join(issues))
            else:
                rep.ok_step("检查函数", f"{len(outputs)} 份输出全部通过")
        bad = sample / "bad"
        if bad.is_dir():  # 故意写错的输出：检查函数必须能发现问题
            missed = [label for label, out in _file_outputs(bad) if not sk.validate(out)]
            if missed:
                rep.fail("反例", f"检查函数没有发现这些反例中的问题：{', '.join(missed)}")
            else:
                rep.ok_step("反例", f"sample/bad/ 中 {len(_files(bad))} 个反例都被发现了")
        if rep.ok and update_registry:
            before = self.registry().get(name)
            if before != sk.version:
                self._set_enabled(name, sk.version)
                rep.registry_changed = (before, sk.version)
        return rep


TEXT_SUFFIXES = {".md", ".txt", ".tex", ".bib", ".csv", ".py", ".yaml", ".yml", ".json", ".jsonl", ".svg"}


def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if p.is_file() and p.name != ".gitkeep")


def _file_outputs(d: Path) -> list[tuple[str, Any]]:
    """检查函数的输入：文本文件给内容（str），其他文件（图片、PDF）给路径。"""
    return [(p.relative_to(d).as_posix(), p.read_text(encoding="utf-8") if p.suffix in TEXT_SUFFIXES else p)
            for p in _files(d)]


def _compare_dirs(expected: Path, actual: Path) -> list[str]:
    """expected/ 中的每个文件都必须产出：.json 按内容比，其他文本按行比（忽略换行符差异），图片和 PDF 只要求存在。"""
    diffs = []
    for e in _files(expected):
        rel = e.relative_to(expected).as_posix()
        a = actual / rel
        if not a.exists():
            diffs.append(f"没有产出 {rel}")
        elif e.suffix == ".json":
            if json.loads(e.read_text(encoding="utf-8")) != json.loads(a.read_text(encoding="utf-8")):
                diffs.append(f"{rel} 的内容与预期不同")
        elif e.suffix in TEXT_SUFFIXES:
            if e.read_text(encoding="utf-8").splitlines() != a.read_text(encoding="utf-8").splitlines():
                diffs.append(f"{rel} 的内容与预期不同")
    return diffs


@dataclass
class VerifyReport:
    name: str
    version: str | None = None
    steps: list[tuple[str, bool, str]] = field(default_factory=list)
    registry_changed: tuple[str | None, str] | None = None

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(ok for _, ok, _ in self.steps)

    def ok_step(self, label: str, detail: str) -> None:
        self.steps.append((label, True, detail))

    def fail(self, label: str, detail: str) -> None:
        self.steps.append((label, False, detail))
