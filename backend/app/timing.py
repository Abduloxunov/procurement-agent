"""Phase timing.

Added before optimising anything, so the work goes where the time actually
is. Guessing which phase is slow is how you spend a day making the fast part
faster.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


class Timer:
    """Accumulates wall time per named phase. Not thread-exclusive: phases
    that run concurrently will overlap, which is the point -- comparing the
    sum of phases against `total` shows how much parallelism actually bought.
    """

    def __init__(self) -> None:
        self.phases: dict[str, float] = {}
        self.counts: dict[str, int] = {}
        self.started = time.perf_counter()

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.phases[name] = self.phases.get(name, 0.0) + elapsed
            self.counts[name] = self.counts.get(name, 0) + 1

    @property
    def total(self) -> float:
        return time.perf_counter() - self.started

    def summary(self) -> dict[str, object]:
        work = sum(self.phases.values())
        return {
            "total_s": round(self.total, 2),
            "work_s": round(work, 2),
            "speedup": round(work / self.total, 2) if self.total > 0 else 1.0,
            "phases": {
                name: {
                    "s": round(seconds, 2),
                    "n": self.counts[name],
                    "avg": round(seconds / self.counts[name], 2),
                }
                for name, seconds in sorted(
                    self.phases.items(), key=lambda kv: -kv[1]
                )
            },
        }

    def report(self) -> str:
        lines = [f"  {'phase':<22} {'total':>7} {'n':>4} {'avg':>7}"]
        lines.append("  " + "-" * 42)
        for name, seconds in sorted(self.phases.items(), key=lambda kv: -kv[1]):
            n = self.counts[name]
            lines.append(f"  {name:<22} {seconds:>6.2f}s {n:>4} {seconds/n:>6.2f}s")
        lines.append("  " + "-" * 42)
        work = sum(self.phases.values())
        lines.append(f"  {'wall clock':<22} {self.total:>6.2f}s")
        lines.append(
            f"  {'work done':<22} {work:>6.2f}s  "
            f"({work / self.total:.1f}x parallel)" if self.total > 0 else ""
        )
        return "\n".join(lines)
