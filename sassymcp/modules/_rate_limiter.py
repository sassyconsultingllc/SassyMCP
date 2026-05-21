"""Per-group rate limiter for SassyMCP tools.

Provides concurrency limiting (asyncio.Semaphore per group) and
rate limiting (token bucket per group). Applied in the audit/tool wrapper.

If the limiter fails for any reason, the tool call proceeds — never blocks
a call due to limiter bugs.
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("sassymcp.ratelimit")


class TokenBucket:
    """Simple token bucket for calls-per-minute rate limiting."""

    def __init__(self, rate: float, capacity: int):
        """
        rate: tokens added per second (calls_per_minute / 60)
        capacity: max burst size
        """
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()

    def acquire(self) -> bool:
        """Try to consume one token. Returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class GroupRateLimiter:
    """Per-group concurrency and rate limiting.

    Two dicts on purpose:
      _configs    — group_name -> int max_concurrent  (set at configure time)
      _semaphores — group_name -> asyncio.BoundedSemaphore (created lazily)

    Splitting them out fixes a race the old design had: storing the
    config int in _semaphores meant two concurrent configure_group calls
    for the same group could both see "not present" and write competing
    semaphores. With separate dicts plus a lock guarding the lazy
    create_semaphore step, only one semaphore is ever instantiated per
    group regardless of caller interleaving.
    """

    def __init__(self):
        self._configs: dict[str, int] = {}
        self._semaphores: dict[str, asyncio.BoundedSemaphore] = {}
        self._buckets: dict[str, TokenBucket] = {}
        # threading.Lock — configure_group and _get_semaphore can be
        # called from worker threads (audit wrapper) and from coroutines
        # alike. The critical section is dict-mutation only; no I/O.
        import threading
        self._lock = threading.Lock()

    def configure_group(self, group_name: str, max_concurrent: int = 10, calls_per_minute: int = 120):
        """Set up limits for a group. Safe to call multiple times.

        Concurrency-safe: writes under a lock so racing callers can't
        produce two semaphores for the same group. The semaphore itself
        is still created lazily inside _get_semaphore() — that has to
        happen on the loop the audit wrapper actually awaits on.
        """
        with self._lock:
            if group_name not in self._configs:
                self._configs[group_name] = max_concurrent
            if group_name not in self._buckets:
                self._buckets[group_name] = TokenBucket(
                    rate=calls_per_minute / 60.0,
                    capacity=max(calls_per_minute // 6, 5),  # 10-second burst window
                )

    def _get_semaphore(self, group_name: str) -> Optional[asyncio.BoundedSemaphore]:
        """Get or lazily create BoundedSemaphore for a group.

        Called from inside the running event loop. The lock ensures only
        one semaphore is constructed even if multiple coroutines race
        the first call for a group.
        """
        sem = self._semaphores.get(group_name)
        if sem is not None:
            return sem
        with self._lock:
            sem = self._semaphores.get(group_name)
            if sem is not None:
                return sem
            cfg = self._configs.get(group_name)
            if cfg is None:
                return None
            sem = asyncio.BoundedSemaphore(cfg)
            self._semaphores[group_name] = sem
            return sem

    async def acquire(self, group_name: str, timeout: float = 30.0) -> bool:
        """Acquire both concurrency slot and rate token for a group.

        Returns True if acquired, False if rate-limited or timed out.
        If the group has no limits configured, always returns True.
        """
        sem = self._get_semaphore(group_name)
        bucket = self._buckets.get(group_name)

        if sem is None and not bucket:
            return True  # no limits configured

        # Acquire concurrency slot first (with timeout)
        if sem is not None:
            try:
                await asyncio.wait_for(sem.acquire(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Concurrency timeout for group '{group_name}'")
                return False

        # Then check rate limit (non-blocking) — refund semaphore if rate-limited
        if bucket and not bucket.acquire():
            logger.warning(f"Rate limit hit for group '{group_name}'")
            if sem is not None:
                sem.release()
            return False

        return True

    def release(self, group_name: str):
        """Release concurrency slot for a group."""
        sem = self._semaphores.get(group_name)
        if sem is not None:
            try:
                sem.release()
            except ValueError:
                logger.warning(f"Over-release detected for group '{group_name}'")


# Module-level singleton
_limiter: Optional[GroupRateLimiter] = None


def get_limiter() -> GroupRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = GroupRateLimiter()
    return _limiter
