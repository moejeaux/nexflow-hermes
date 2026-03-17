"""
Rate limiting and TTL caching for API providers.

Prevents burning API credits on redundant calls by caching responses
and enforcing per-provider request rate limits.
"""

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from functools import wraps
from typing import Any

from cachetools import TTLCache


class RateLimiter:
    """Token-bucket rate limiter. Thread-safe via asyncio.Lock."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a request slot is available."""
        async with self._lock:
            now = time.monotonic()
            # Prune expired timestamps
            self._timestamps = [
                t for t in self._timestamps
                if now - t < self.window_seconds
            ]
            if len(self._timestamps) >= self.max_requests:
                # Wait until the oldest request expires
                sleep_time = self.window_seconds - (now - self._timestamps[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                self._timestamps = self._timestamps[1:]
            self._timestamps.append(time.monotonic())


# Provider-specific rate limiters
# Alchemy: generous limits on paid plans, but we still cap to be safe
# Bitquery: 10 req/min on free tier
RATE_LIMITERS: dict[str, RateLimiter] = {
    "alchemy": RateLimiter(max_requests=25, window_seconds=1),
    "bitquery": RateLimiter(max_requests=10, window_seconds=60),
}

# Response caches — keyed by provider, with different TTLs
# Alchemy data: 15-second TTL (blockchain moves fast)
# Bitquery analytics: 60-second TTL (aggregated data changes slower)
CACHES: dict[str, TTLCache] = {
    "alchemy": TTLCache(maxsize=500, ttl=15),
    "bitquery": TTLCache(maxsize=200, ttl=60),
}


def _make_cache_key(func_name: str, args: tuple, kwargs: dict) -> str:
    """Create a deterministic cache key from function name and arguments."""
    key_data = {
        "func": func_name,
        "args": [str(a) for a in args],
        "kwargs": {k: str(v) for k, v in sorted(kwargs.items())},
    }
    raw = json.dumps(key_data, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached(provider: str):
    """Decorator that caches async function results with rate limiting.

    Usage:
        @cached("alchemy")
        async def get_block(block_number: int) -> dict: ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache = CACHES.get(provider)
            limiter = RATE_LIMITERS.get(provider)
            cache_key = _make_cache_key(func.__name__, args, kwargs)

            # Check cache first
            if cache is not None and cache_key in cache:
                return cache[cache_key]

            # Rate limit the actual API call
            if limiter is not None:
                await limiter.acquire()

            result = await func(*args, **kwargs)

            # Store in cache
            if cache is not None and result is not None:
                cache[cache_key] = result

            return result
        return wrapper
    return decorator
