"""CPU and memory utilisation of the server, for API responses and GET /api/metrics.

Numbers are for the whole server process plus its child processes (the ffmpeg encoders), so
when several stories are generated at once, a request's figures include the others' work too.
"""
import asyncio
import os
import resource
import time
from typing import Optional

import psutil

_proc = psutil.Process()
CPU_COUNT = psutil.cpu_count() or 1


def _mb(n: float) -> float:
    return round(n / (1024 * 1024), 1)


def _tree_rss() -> int:
    """Resident memory of the server and all its child processes, in bytes."""
    total = _proc.memory_info().rss
    for child in _proc.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            pass
    return total


def _cpu_seconds() -> float:
    """CPU time used by the server so far, including finished child processes (ffmpeg).

    Uses getrusage rather than psutil: psutil reports child CPU time as 0 on macOS.
    """
    own = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


def _limit_bytes() -> Optional[int]:
    """The container's memory limit (cgroup), e.g. 512 MB on Render; None when not limited."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = open(path).read().strip()
            if value.isdigit() and int(value) < 1 << 50:
                return int(value)
        except OSError:
            pass
    return None


def server_stats() -> dict:
    """Current utilisation: system CPU (sampled over 0.2 s), memory, container limit if any."""
    vm = psutil.virtual_memory()
    limit = _limit_bytes()
    rss = _tree_rss()
    out = {
        "cpu_cores": CPU_COUNT,
        "cpu_percent_system": psutil.cpu_percent(interval=0.2),   # sampled over 0.2 s
        "load_average_1m": round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
        "server_memory_mb": _mb(rss),
        "system_memory_used_percent": vm.percent,
        "system_memory_total_mb": _mb(vm.total),
    }
    if limit:
        out["container_memory_limit_mb"] = _mb(limit)
        out["container_memory_used_percent"] = round(100 * rss / limit, 1)
    return out


class RequestMeter:
    """Measures one request: wall time, CPU seconds used, and peak memory while it ran."""

    def __init__(self, sample_every: float = 0.25):
        self.sample_every = sample_every
        self.start_wall = time.perf_counter()
        self.start_cpu = _cpu_seconds()
        self.start_rss = _tree_rss()
        self.peak_rss = self.start_rss
        self._task = asyncio.get_running_loop().create_task(self._sample())

    async def _sample(self):
        while True:
            try:
                self.peak_rss = max(self.peak_rss, _tree_rss())
            except psutil.Error:
                pass
            await asyncio.sleep(self.sample_every)

    def finish(self) -> dict:
        if getattr(self, "_result", None):
            return self._result
        self._task.cancel()
        wall = time.perf_counter() - self.start_wall
        cpu = _cpu_seconds() - self.start_cpu
        end_rss = _tree_rss()
        self.peak_rss = max(self.peak_rss, end_rss)
        limit = _limit_bytes()
        out = {
            "response_time_s": round(wall, 2),
            "cpu_seconds": round(cpu, 2),
            # average number of cores busy during the request (1.0 = one full core)
            "cpu_cores_used_avg": round(cpu / wall, 2) if wall > 0 else 0.0,
            "cpu_percent_avg": round(100 * cpu / wall / CPU_COUNT, 1) if wall > 0 else 0.0,
            "memory_start_mb": _mb(self.start_rss),
            "memory_peak_mb": _mb(self.peak_rss),
            "memory_end_mb": _mb(end_rss),
            "cpu_cores": CPU_COUNT,
        }
        if limit:
            out["container_memory_limit_mb"] = _mb(limit)
            out["memory_peak_percent_of_limit"] = round(100 * self.peak_rss / limit, 1)
        self._result = out
        return out
