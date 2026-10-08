"""跨平台的进程工具（Mac / Linux / Windows），基于 psutil。

不要用 os.kill(pid, 0) 判断进程是否存在：在 Windows 上它会直接结束那个进程。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import psutil


def detached_kwargs() -> dict[str, Any]:
    """让子进程脱离当前进程（后台重启、终端关闭都不影响它），并自成一个进程组。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def new_group_kwargs() -> dict[str, Any]:
    """子进程自成一个进程组（取消时只结束这一组），但仍可通过管道读取它的输出。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def pid_matches(pid: int | None, run_id: str) -> bool:
    """pid 对应的进程还活着，且命令行或工作目录中包含 run_id（避免 pid 被系统复用后误判）。

    包装器的命令行含运行目录；实验进程的工作目录是 runs/<run_id>/code。
    """
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        if p.status() == psutil.STATUS_ZOMBIE:
            return False
        if run_id in " ".join(p.cmdline()):
            return True
        try:
            return run_id in p.cwd()
        except psutil.AccessDenied:
            return False
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True  # 看不到命令行时保守地认为还在


def kill_tree(pid: int, timeout: float = 10.0) -> None:
    """先礼后兵：结束进程及其全部子进程（terminate，超时后 kill）。"""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = [*root.children(recursive=True), root]
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass


def tree_usage(pid: int) -> tuple[float, float]:
    """进程树当前的 (CPU 秒数, 内存 MB)。进程已结束时返回 (0, 0)。"""
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0.0, 0.0
    cpu = mem = 0.0
    for p in procs:
        try:
            t = p.cpu_times()
            cpu += t.user + t.system
            mem += p.memory_info().rss / (1024 * 1024)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return cpu, mem
