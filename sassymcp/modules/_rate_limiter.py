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
    """Per-group concurrency and rate limiting."""

    def __init__(self):
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._buckets: dict[str, TokenBucket] = {}

    def configure_group(self, group_name: str, max_concurrent: int = 10, calls_per_minute: int = 120):
        """Set up limits for a group. Safe to call multiple times.

        Note: Semaphores are created lazily on first acquire() to ensure
        they are bound to the correct event loop (uvicorn's).
        """
        if group_name not in self._semaphores:
            # Store config, create semaphore lazily in acquire()
            self._semaphores[group_name] = max_concurrent  # store int, create lazily
        if group_name not in self._buckets:
            self._buckets[group_name] = TokenBucket(
                rate=calls_per_minute / 60.0,
                capacity=max(calls_per_minute // 6, 5),  # 10-second burst window
            )

    def _get_semaphore(self, group_name: str) -> Optional[asyncio.BoundedSemaphore]:
        """Get or lazily create BoundedSemaphore for a group."""
        val = self._semaphores.get(group_name)
        if val is None:
            return None
        if isinstance(val, int):
            # Lazy creation — now we're inside the event loop
            sem = asyncio.BoundedSemaphore(val)
            self._semaphores[group_name] = sem
            return sem
        return val

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
        if sem is not None and isinstance(sem, asyncio.BoundedSemaphore):
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
