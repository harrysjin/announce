"""Bounded single-process runtime helpers for the temporary PDF workspace.

These controls are intentionally process-local. The app must use one ASGI worker
until session storage and the analysis queue move to shared infrastructure.
Never include exception messages, filenames or credentials in runtime errors.
"""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import math
import time
from typing import Awaitable, Callable

ACTIVE_STATES = frozenset({"queued", "running"})


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after: int = 0


class SlidingWindowLimiter:
    """Fixed-size client-address rate limiter; never trust X-Forwarded-For here.

    Unknown clients are rejected when the bounded table is full. Evicting a live
    client bucket would let a rotating caller reset somebody else's allowance.
    """

    def __init__(self, limit: int, window_seconds: float, *, max_clients: int = 2048,
                 clock: Callable[[], float] = time.monotonic):
        if limit < 1 or window_seconds <= 0 or max_clients < 1:
            raise ValueError("rate limit bounds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self.clock = clock
        self._events: dict[str, deque[float]] = {}

    def check(self, client: str) -> RateDecision:
        now = self.clock()
        cutoff = now - self.window_seconds
        # At most max_clients buckets, each at most limit timestamps.
        for key in list(self._events):
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                del self._events[key]
        events = self._events.get(client)
        if events is None:
            if len(self._events) >= self.max_clients:
                soonest = min(e[0] for e in self._events.values()) + self.window_seconds
                return RateDecision(False, max(1, math.ceil(soonest - now)))
            events = self._events[client] = deque()
        if len(events) >= self.limit:
            return RateDecision(False, max(1, math.ceil(events[0] + self.window_seconds - now)))
        events.append(now)
        return RateDecision(True)


class AnalysisGate:
    """Bound analysis workers, waiting time and execution time.

    The worker may catch CancelledError itself (the existing analysis loop does),
    so explicit timeout handling uses asyncio.wait followed by cancellation rather
    than assuming asyncio.wait_for will raise after cancellation is swallowed.
    """

    def __init__(self, concurrency: int = 2, *, run_timeout: float = 600,
                 queue_timeout: float = 300):
        if concurrency < 1 or run_timeout <= 0 or queue_timeout <= 0:
            raise ValueError("analysis bounds must be positive")
        self.concurrency = concurrency
        self.run_timeout = run_timeout
        self.queue_timeout = queue_timeout
        self._slots = asyncio.Semaphore(concurrency)
        self.active = 0
        self.waiting = 0

    async def run(self, job: dict, worker: Callable[[dict], Awaitable[None]],
                  *, on_done: Callable[[], None] | None = None) -> None:
        acquired = False
        waiting = True
        child = None
        self.waiting += 1
        job["analysis"].update(state="queued", message="앞선 작업을 기다리고 있습니다. 중단할 수 있습니다.")
        try:
            try:
                await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
            except TimeoutError:
                job["analysis"].update(state="error", error_code="queue_timeout",
                                       message="작업 대기 시간이 지났습니다. 잠시 후 다시 시도해 주세요.")
                return
            acquired = True
            self.waiting -= 1
            waiting = False
            self.active += 1
            job["analysis"].pop("error_code", None)
            job["analysis"].update(state="running", message="공식 사전에서 발음 근거를 확인하고 있습니다.")
            child = asyncio.create_task(worker(job))
            done, _ = await asyncio.wait({child}, timeout=self.run_timeout)
            if not done:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)
                job["analysis"].update(state="error", error_code="analysis_timeout",
                                       message="분석 시간이 한도를 초과했습니다. 확인된 결과는 유지됩니다. 문서를 나누거나 다시 시도해 주세요.")
                return
            await child  # Retrieve and sanitize worker failures below.
        except asyncio.CancelledError:
            if child is not None:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)
            job["analysis"].update(state="cancelled", message="분석을 중단했습니다. 확인한 결과는 유지됩니다.")
        except Exception:
            # A transport exception can carry a full URL including the API key.
            # Persist only a fixed error code/message; keep partial evidence.
            job["analysis"].update(state="error", error_code="analysis_internal",
                                   message="분석 중 오류가 발생했습니다. 확인된 결과는 유지됩니다. 다시 시도해 주세요.")
        finally:
            if waiting:
                self.waiting -= 1
            if acquired:
                self.active -= 1
                self._slots.release()
            if on_done is not None:
                on_done()


def progress_snapshot(job: dict, *, gate: AnalysisGate | None = None) -> dict:
    """Small polling response: no pages, glyph coordinates or dictionary bodies."""
    from .pdf import counts
    result = {"id": job["id"], "revision": job.get("revision", 0),
              "analysis": deepcopy(job["analysis"]), "counts": counts(job)}
    if gate is not None:
        result["capacity"] = {"active": gate.active, "waiting": gate.waiting,
                              "concurrent_limit": gate.concurrency}
    return result
