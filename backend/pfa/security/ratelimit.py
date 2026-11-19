"""In-process rate limiting with increasing backoff.

Deployments with more than one instance must back this with a shared store; the
interface is deliberately small so it can be swapped.  Limits exist for login,
password reset and token endpoints.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    failures: list[float] = field(default_factory=list)
    blocked_until: float = 0.0


class RateLimiter:
    def __init__(self, *, limit: int, window_seconds: int, base_backoff_seconds: int = 1, max_backoff_seconds: int = 300) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._buckets: dict[str, _Bucket] = {}

    def _bucket(self, key: str, now: float) -> _Bucket:
        bucket = self._buckets.setdefault(key, _Bucket())
        cutoff = now - self.window_seconds
        bucket.failures = [moment for moment in bucket.failures if moment >= cutoff]
        return bucket

    def check(self, key: str, *, now: float | None = None) -> int:
        """Return 0 when allowed, otherwise the number of seconds to wait."""
        now = time.monotonic() if now is None else now
        bucket = self._bucket(key, now)
        if bucket.blocked_until > now:
            return int(bucket.blocked_until - now) + 1
        return 0

    def record_failure(self, key: str, *, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        bucket = self._bucket(key, now)
        bucket.failures.append(now)
        if len(bucket.failures) >= self.limit:
            over = len(bucket.failures) - self.limit + 1
            backoff = min(self.base_backoff_seconds * (2 ** over), self.max_backoff_seconds)
            bucket.blocked_until = now + backoff
        return int(bucket.blocked_until - now) if bucket.blocked_until > now else 0

    def reset(self, key: str) -> None:
        self._buckets.pop(key, None)


login_limiter = RateLimiter(limit=5, window_seconds=300, base_backoff_seconds=2, max_backoff_seconds=900)
password_reset_limiter = RateLimiter(limit=3, window_seconds=900, base_backoff_seconds=5, max_backoff_seconds=1800)
#: Per account: protects the mailbox owner from being flooded with activation
#: links (an attacker rotating source addresses must not bypass this).
verification_resend_limiter = RateLimiter(
    limit=3, window_seconds=900, base_backoff_seconds=5, max_backoff_seconds=1800
)
#: Per source address: bounds enumeration attempts from one client.
verification_resend_ip_limiter = RateLimiter(
    limit=10, window_seconds=900, base_backoff_seconds=5, max_backoff_seconds=1800
)
token_limiter = RateLimiter(limit=60, window_seconds=60, base_backoff_seconds=1, max_backoff_seconds=60)
register_limiter = RateLimiter(limit=10, window_seconds=3600, base_backoff_seconds=5, max_backoff_seconds=3600)


def reset_all() -> None:
    for limiter in (
        login_limiter,
        password_reset_limiter,
        verification_resend_limiter,
        verification_resend_ip_limiter,
        token_limiter,
        register_limiter,
    ):
        limiter._buckets.clear()
