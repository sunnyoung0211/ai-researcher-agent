"""SSE 推送（详细设计 1 第 9.5 节）的简化版：每秒检查一次事件日志和项目状态。

只推 state 和 event 两类（approval / question / run / budget 的变化都会以 event 的形式出现）。
SSE 只做“通知”，REST 才是数据来源。断线重连时带 Last-Event-ID（= 事件 seq），服务端补发之后的 event。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .deps import manager
from .manager import ProjectManager

router = APIRouter(prefix="/api/projects/{pid}", tags=["stream"])


def _msg(event: str, data: dict, id: int | None = None) -> str:
    head = f"id: {id}\n" if id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/stream")
async def stream(pid: str, request: Request, m: ProjectManager = Depends(manager)) -> StreamingResponse:
    p = m.get(pid)
    try:
        last_seq = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        last_seq = 0

    async def gen() -> AsyncIterator[str]:
        nonlocal last_seq
        last_state = None
        while not await request.is_disconnected():
            st = m.status(pid)
            brief = {"state": st.state.value, "stage_label": st.stage_label, "blocking_reason": st.blocking_reason}
            if brief != last_state:
                last_state = brief
                yield _msg("state", brief)
            for ev in p.events.query(after_seq=last_seq, limit=500):
                last_seq = ev.seq
                yield _msg("event", ev.model_dump(mode="json"), id=ev.seq)
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
