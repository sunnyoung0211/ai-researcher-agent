"""录制/回放缓存（详细设计 1 第 6.6 节）。llm_cache: record 时存响应，replay 时命中直接返回。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from airesearcher.core.fsutil import atomic_write_json, read_json
from airesearcher.core.workspace import air_home


class CacheMiss(Exception):
    pass


class LLMCache:
    def __init__(self, directory: Path | None = None):
        self.dir = directory or (air_home() / "llm_cache")

    @staticmethod
    def key(model: str, messages: list[dict], schema: str | None, temperature: float | None) -> str:
        blob = json.dumps({"model": model, "messages": messages, "schema": schema, "tools": None,
                           "temperature": temperature}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> dict:
        p = self.dir / f"{key}.json"
        if not p.exists():
            raise CacheMiss(key)
        return read_json(p)

    def put(self, key: str, value: dict) -> None:
        atomic_write_json(self.dir / f"{key}.json", value)
