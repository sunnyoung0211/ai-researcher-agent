"""Markdown + YAML 头部（三条横线之间）的读写，selected.md 和 experiment_plan.md 共用。"""

from __future__ import annotations

from typing import Any

import yaml


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """返回 (YAML 区块解析结果, 正文)。没有 YAML 区块时抛 ValueError。"""
    if not text.startswith("---"):
        raise ValueError("缺少开头的 YAML 区块（第一行应为 ---）")
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            head = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            data = yaml.safe_load(head) or {}
            if not isinstance(data, dict):
                raise ValueError("YAML 区块必须是键值对")
            return data, body
    raise ValueError("YAML 区块没有结束的 ---")


def render_front_matter(data: dict[str, Any], body: str) -> str:
    head = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    return f"---\n{head}---\n\n{body.lstrip()}"
