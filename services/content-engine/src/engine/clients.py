"""Clients for the LLM gateway and the catalog MCP server."""

import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ValidationError

from .models import json_schema

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")
GATEWAY_KEY = os.getenv("GATEWAY_KEY", "")
CATALOG_MCP_URL = os.getenv("CATALOG_MCP_URL", "http://localhost:8001/mcp")
CATALOG_TOKEN = os.getenv("CATALOG_TOKEN", "")

T = TypeVar("T", bound=BaseModel)


@dataclass
class Spend:
    """Tokens and cost accumulated across one job's model calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def add(self, step: str, r: dict) -> None:
        u = r["usage"]
        self.prompt_tokens += u["prompt_tokens"]
        self.completion_tokens += u["completion_tokens"]
        self.cost_usd += u["cost_usd"]
        self.calls.append({"step": step, "tier": r["tier"], "deployment": r["deployment"], "cached": u["cached"],
                           "latency_ms": u["latency_ms"], "cost_usd": u["cost_usd"],
                           "attempts": r["route"]["attempts"]})


class ModelOutputError(Exception):
    pass


class ContentBlocked(Exception):
    """The platform content filter refused the request; needs a human, not a retry."""


class Gateway:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120,
                                      headers={"Authorization": f"Bearer {GATEWAY_KEY}"})

    async def structured(self, *, task: str, feature: str, system: str, user: str, schema: type[T], name: str,
                         spend: Spend, step: str, temperature: float = 0.3, max_tokens: int = 1500) -> T:
        r = await self.http.post("/v1/chat/completions", json={
            "task": task, "feature": feature, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": json_schema(schema, name)})
        if r.status_code == 422 and r.json().get("title", "").startswith("Blocked by content filter"):
            raise ContentBlocked(r.json()["title"])
        r.raise_for_status()
        body = r.json()
        spend.add(step, body)
        if body["finish_reason"] == "length":
            raise ModelOutputError("Model output was cut off (max_tokens)")
        try:
            return schema.model_validate(json.loads(body["content"]))
        except (json.JSONDecodeError, ValidationError) as e:
            raise ModelOutputError(f"Output did not match {name}: {e}") from e

    async def usage(self, group_by: str, days: int) -> Any:
        r = await self.http.get("/v1/usage", params={"group_by": group_by, "days": days})
        r.raise_for_status()
        return r.json()


@asynccontextmanager
async def catalog():
    """An authenticated MCP session to the catalog server."""
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {CATALOG_TOKEN}"}, timeout=60)
    async with http, Client(streamable_http_client(CATALOG_MCP_URL, http_client=http)) as client:
        yield CatalogTools(client)


class CatalogTools:
    """Typed wrappers; the agent can only reach the catalog through these."""

    def __init__(self, client: Client):
        self.client = client

    async def _call(self, tool: str, args: dict) -> Any:
        result = await self.client.call_tool(tool, args)
        if result.is_error:
            raise RuntimeError(f"{tool} failed: {' '.join(getattr(c, 'text', '') for c in result.content)}")
        if result.structured_content is not None:
            data = result.structured_content
            return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
        return json.loads(result.content[0].text)

    async def get_product(self, sku: str) -> dict:
        return await self._call("get_product", {"sku": sku})

    async def search_products(self, query: str, category: str | None = None, limit: int = 20) -> list[dict]:
        return await self._call("search_products", {"query": query, "category": category, "limit": limit})

    async def search_datasheet(self, sku: str, query: str, k: int = 4) -> dict:
        return await self._call("search_datasheet", {"sku": sku, "query": query, "k": k})

    async def save_draft(self, sku: str, content: dict, job_id: str, approved_by: str) -> dict:
        return await self._call("save_draft", {"sku": sku, "content": content, "job_id": job_id,
                                               "approved_by": approved_by})

    async def list_categories(self) -> list[dict]:
        return await self._call("list_categories", {})
