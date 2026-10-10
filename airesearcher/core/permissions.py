"""权限守卫（详细设计 1 第 4.4 节）。网关、文献源、执行器、编码 Agent 的文件工具都必须先调用 guard。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from .errors import PermissionDenied
from .events import Events
from .models.project import Permissions as PermissionConfig
from .workspace import air_home

Action = Literal["network", "exec", "write", "publish"]


class Permissions:
    def __init__(self, root: Path, events: Events, config: Callable[[], PermissionConfig]):
        self.root = Path(root).resolve()
        self.events = events
        self._config = config
        self._granted: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def _deny(self, action: str, target: str, reason: str, actor: str) -> None:
        self.events.append(
            "permission.denied", f"拒绝 {action} {target}：{reason}", actor=actor,
            data={"action": action, "target": target, "reason": reason},
        )
        raise PermissionDenied(f"权限拒绝：{action} {target}（{reason}）", {"action": action, "target": target})

    def _write_roots(self) -> list[Path]:
        roots = []
        for r in self._config().write.roots:
            r = r.replace("${AIR_HOME}", str(air_home()))
            p = Path(r).expanduser()
            roots.append((p if p.is_absolute() else self.root / p).resolve())
        return roots

    def guard(self, action: Action, target: str, actor: str = "agent", extra_allow: set[str] | None = None) -> None:
        """extra_allow：调用方额外允许的域名（如用户在模型配置中登记的接口），只对 network 生效。"""
        cfg = self._config()
        if action == "network":
            host = target.lower().split("://")[-1].split("/")[0].split(":")[0]
            allow = [*cfg.network.allow, *(extra_allow or ())]
            if not any(host == d or host.endswith("." + d) for d in allow):
                self._deny(action, target, "域名不在 permissions.network.allow 白名单中", actor)
        elif action == "exec":
            if target != "local" or not cfg.exec.local:
                self._deny(action, target, "只允许本地执行，且 permissions.exec.local 为真", actor)
        elif action == "write":
            p = Path(target)
            p = (p if p.is_absolute() else self.root / p).resolve()  # resolve 会展开 ../ 和符号链接
            if not any(p == r or r in p.parents for r in self._write_roots()):
                self._deny(action, target, "路径不在可写范围内", actor)
            if p == self.root / "project.yaml" and actor not in ("system", "engine"):
                self._deny(action, target, "project.yaml 只允许主干修改", actor)
            runs = self.root / "runs"
            if (p == runs or runs in p.parents) and actor not in ("executor", "system"):
                self._deny(action, target, "runs/ 下只允许执行器写", actor)
        elif action == "publish":
            self._deny(action, target, "本期不允许对外发布", actor)
        else:
            self._deny(str(action), target, "未知的权限类别", actor)

        key = (action, target if action != "write" else "write")
        with self._lock:
            first = key not in self._granted
            self._granted.add(key)
        if first:
            self.events.append(
                "permission.granted", f"允许 {action} {target}", actor=actor,
                data={"action": action, "target": target},
            )
