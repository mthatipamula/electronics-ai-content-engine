"""Job queue. Redis Streams locally; Azure Service Bus (passwordless, via managed identity) in Azure.

Delivery is at-least-once in both; the worker is idempotent because a job can only be claimed once.
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from typing import Protocol

from common.telemetry import log

Handler = Callable[[dict], Awaitable[None]]
QUEUE = "content-jobs"


class Bus(Protocol):
    async def publish(self, message: dict) -> None: ...
    async def consume(self, handler: Handler) -> None: ...
    async def close(self) -> None: ...


class RedisStreamsBus:
    def __init__(self, url: str):
        import redis.asyncio as redis

        # Socket timeout must exceed the blocking read below, or every idle poll looks like a failure.
        self.redis = redis.from_url(url, decode_responses=True, socket_timeout=30, health_check_interval=30)
        self.group, self.consumer = "content-engine", os.getenv("HOSTNAME", "worker-1")

    async def publish(self, message: dict) -> None:
        await self.redis.xadd(QUEUE, {"body": json.dumps(message)}, maxlen=10_000)

    async def consume(self, handler: Handler) -> None:
        try:
            await self.redis.xgroup_create(QUEUE, self.group, id="0", mkstream=True)
        except Exception as e:  # noqa: BLE001 - group already exists
            if "BUSYGROUP" not in str(e):
                raise
        while True:
            # Re-deliver messages another worker took but never acknowledged (crashed mid-job).
            _, stale, _ = await self.redis.xautoclaim(QUEUE, self.group, self.consumer, min_idle_time=300_000,
                                                      count=10)
            fresh = await self.redis.xreadgroup(self.group, self.consumer, {QUEUE: ">"}, count=4, block=5000)
            batch = stale + [m for _, msgs in fresh for m in msgs]
            await asyncio.gather(*(self._handle(mid, fields, handler) for mid, fields in batch))

    async def _handle(self, mid: str, fields: dict, handler: Handler) -> None:
        try:
            await handler(json.loads(fields["body"]))
        except Exception:  # noqa: BLE001 - the job records its own failure; don't poison the stream
            log.exception("job handler failed", message_id=mid)
        await self.redis.xack(QUEUE, self.group, mid)

    async def close(self) -> None:
        await self.redis.aclose()


class ServiceBusBus:
    def __init__(self, namespace: str):
        from azure.identity.aio import DefaultAzureCredential
        from azure.servicebus.aio import ServiceBusClient

        self.credential = DefaultAzureCredential()
        self.client = ServiceBusClient(namespace, self.credential)

    async def publish(self, message: dict) -> None:
        from azure.servicebus import ServiceBusMessage

        async with self.client.get_queue_sender(QUEUE) as sender:
            await sender.send_messages(ServiceBusMessage(json.dumps(message), message_id=message["job_id"]))

    async def consume(self, handler: Handler) -> None:
        async with self.client.get_queue_receiver(QUEUE, max_wait_time=30) as receiver:
            while True:
                for msg in await receiver.receive_messages(max_message_count=4, max_wait_time=5):
                    try:
                        await handler(json.loads(str(msg)))
                        await receiver.complete_message(msg)
                    except Exception:  # noqa: BLE001 - after max deliveries Service Bus dead-letters it
                        log.exception("job handler failed", message_id=msg.message_id)
                        await receiver.abandon_message(msg)

    async def close(self) -> None:
        await self.client.close()
        await self.credential.close()


def bus() -> Bus:
    if ns := os.getenv("SERVICEBUS_NAMESPACE"):
        return ServiceBusBus(ns)
    return RedisStreamsBus(os.getenv("REDIS_URL", "redis://localhost:6379/1"))
