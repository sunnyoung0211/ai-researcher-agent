"""项目级文件锁 .state/lock：同一项目同一时刻只有一个引擎（跨进程）。

Mac/Linux 用 fcntl.flock，Windows 用 msvcrt.locking；锁随进程退出自动释放。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .errors import ProjectLocked

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class ProjectLock:
    def __init__(self, root: Path):
        self.path = Path(root) / ".state" / "lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_lock(fd):
            os.close(fd)
            raise ProjectLocked(f"项目 {self.path.parents[1].name} 正被另一个进程（后台或 air dev）使用")
        # 锁住的是第 1 个字节；pid 写在后面，供人排查（Windows 上不能写被锁住的字节）
        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            try:
                _unlock(self._fd)
            except OSError:
                pass
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> ProjectLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
