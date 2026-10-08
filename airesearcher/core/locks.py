"""项目级文件锁 .state/lock：同一项目同一时刻只有一个引擎（跨进程）。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from .errors import ProjectLocked


class ProjectLock:
    def __init__(self, root: Path):
        self.path = Path(root) / ".state" / "lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise ProjectLocked(f"项目 {self.path.parents[1].name} 正被另一个进程（后台或 air dev）使用") from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
