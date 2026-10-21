"""Model providers. Azure OpenAI in every real environment; a deterministic mock for offline tests and CI."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

import openai

from .config import settings


@dataclass
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str | None


class Retryable(Exception):
    """Throttling, timeouts and server errors: worth trying the next tier."""


class ContentFiltered(Exception):
    """Azure's content filter (DefaultV2: harm categories, Prompt Shields) blocked the prompt or the output.
    Not retried on another tier: the same input would be blocked again."""


class Provider(Protocol):
    async def chat(self, deployment: str, messages: list[dict], response_format: dict | None,
                   temperature: float, max_tokens: int) -> Completion: ...

    async def embed(self, deployment: str, texts: list[str], dimensions: int) -> tuple[list[list[float]], int]: ...


class AzureOpenAIProvider:
    def __init__(self) -> None:
        s = settings()
        if not s.azure_openai_endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
        kwargs: dict[str, Any] = {"azure_endpoint": s.azure_openai_endpoint, "api_version": s.azure_openai_api_version,
                                  "timeout": s.request_timeout_seconds, "max_retries": 1}
        if s.azure_openai_api_key:
            kwargs["api_key"] = s.azure_openai_api_key
        else:  # passwordless: managed identity in Azure, developer login locally
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        self.client = openai.AsyncAzureOpenAI(**kwargs)

    async def chat(self, deployment, messages, response_format, temperature, max_tokens) -> Completion:
        try:
            kwargs: dict[str, Any] = {"model": deployment, "messages": messages, "temperature": temperature,
                                      "max_tokens": max_tokens}
            if response_format:
                kwargs["response_format"] = response_format
            r = await self.client.chat.completions.create(**kwargs)
        except openai.BadRequestError as e:
            if getattr(e, "code", None) == "content_filter" or "content_filter" in str(e):
                raise ContentFiltered("prompt blocked by content filter") from e
            raise
        except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                openai.InternalServerError) as e:
            raise Retryable(type(e).__name__) from e
        choice = r.choices[0]
        if choice.finish_reason == "content_filter":
            raise ContentFiltered("output blocked by content filter")
        return Completion(choice.message.content or "", r.usage.prompt_tokens, r.usage.completion_tokens,
                          choice.finish_reason)

    async def embed(self, deployment, texts, dimensions):
        try:
            r = await self.client.embeddings.create(model=deployment, input=texts, dimensions=dimensions)
        except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                openai.InternalServerError) as e:
            raise Retryable(type(e).__name__) from e
        return [d.embedding for d in r.data], r.usage.prompt_tokens


class MockProvider:
    """Deterministic stand-in so the whole pipeline runs without a model. Not a quality signal."""

    async def chat(self, deployment, messages, response_format, temperature, max_tokens) -> Completion:
        text = "\n".join(m["content"] for m in messages)
        if response_format and response_format.get("type") == "json_schema":
            spec = response_format["json_schema"]
            obj = _mock_object(spec.get("name", ""), spec["schema"], text)
            content = json.dumps(obj)
        else:
            content = "This is a mock response."
        return Completion(content, _approx_tokens(text), _approx_tokens(content), "stop")

    async def embed(self, deployment, texts, dimensions):
        return [_hash_embedding(t, dimensions) for t in texts], sum(_approx_tokens(t) for t in texts)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _hash_embedding(text: str, dims: int) -> list[float]:
    """Bag-of-words hashing: similar wording gives similar vectors, which is enough to exercise retrieval."""
    v = [0.0] * dims
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(word.encode(), usedforsecurity=False).hexdigest(), 16)
        v[h % dims] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _facts(text: str) -> dict:
    # The last block is the data; earlier mentions are the instructions talking about the tag.
    for block in reversed(re.findall(r"<product_facts>(\{.*?\})</product_facts>", text, re.S)):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return {}


def _mock_object(name: str, schema: dict, text: str) -> Any:
    facts = _facts(text)
    if name == "product_content" and facts:
        specs = facts.get("specs", {})
        items = list(specs.items())[:5]
        return {
            "title": f"{facts.get('brand', '')} {facts.get('name', '')}".strip()[:150],
            "short_description": f"{facts.get('name', 'This product')} for {facts.get('category', 'electronics')} "
                                 f"applications."[:300],
            "long_description": " ".join(f"{k.replace('_', ' ').capitalize()}: {v}." for k, v in items)[:2000],
            "bullets": [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in items][:6] or ["See specifications"],
            "seo_keywords": [w for w in re.findall(r"[a-z0-9-]+", facts.get("name", "").lower())][:8],
            "cited_spec_keys": [k for k, _ in items],
        }
    if name == "faithfulness_verdict":
        return {"claims": [], "unsupported_count": 0, "score": 1.0, "explanation": "mock: not evaluated"}
    return _fill(schema)


def _fill(schema: dict) -> Any:
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: _fill(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return []
    return {"string": "mock", "integer": 0, "number": 0.0, "boolean": False}.get(t)


def provider() -> Provider:
    return AzureOpenAIProvider() if settings().provider == "azure" else MockProvider()
