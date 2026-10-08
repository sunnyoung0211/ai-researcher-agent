"""路由共用的依赖：从 app.state 取 ProjectManager。"""

from __future__ import annotations

from fastapi import Request

from .manager import ProjectManager


def manager(request: Request) -> ProjectManager:
    return request.app.state.manager
