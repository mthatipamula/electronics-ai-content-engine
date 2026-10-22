"""Response cache and rate-limit counters: Redis when configured, otherwise in-process memory.

In-memory is correct for a single replica and costs nothing; with several replicas each keeps its own cache and
counters, so set REDIS_URL (Azure Managed Redis) before scaling out.
"""

import time

import redis.asyncio as redis


class MemoryKV:
    def __init__(self) -> None:
        self.data: dict[str, tuple[str | int, float | None]] = {}

    def _live(self, key: str):
        item = self.data.get(key)
        if item and item[1] is not None and item[1] < time.monotonic():
            del self.data[key]
            return None
        return item

    async def get(self, key: str) -> str | None:
        item = self._live(key)
        return str(item[0]) if item else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if len(self.data) > 10_000:  # crude bound on memory
            self.data.clear()
        self.data[key] = (value, time.monotonic() + ex if ex else None)

    async def incr(self, key: str) -> int:
        item = self._live(key)
        value = int(item[0]) + 1 if item else 1
        self.data[key] = (value, item[1] if item else None)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        if item := self._live(key):
            self.data[key] = (item[0], time.monotonic() + seconds)

    async def aclose(self) -> None:
        pass


def connect(url: str | None):
    return redis.from_url(url, decode_responses=True) if url else MemoryKV()
