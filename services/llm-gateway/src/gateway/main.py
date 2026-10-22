"""LLM gateway: the only component that talks to model providers.

Every call is authenticated with a per-application virtual key, checked against that app's monthly budget and
rate limit, scrubbed of personal data, routed to a model tier by task (with fallback), cached when deterministic,
and written to the usage ledger for cost attribution.
"""

import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from common import pii, telemetry
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace

from . import kv
from .config import plan, routing, settings
from .providers import ContentFiltered, Retryable, provider
from .schemas import ChatRequest, ChatResponse, EmbeddingRequest, EmbeddingResponse, KeyRequest, Usage
from .store import Store

telemetry.setup("llm-gateway")
tracer = trace.get_tracer("llm-gateway")
log = telemetry.log


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings()
    app.state.store = await Store.connect(s.database_url)
    app.state.cache = kv.connect(s.redis_url)
    app.state.provider = provider()
    for spec in json.loads(os.getenv("BOOTSTRAP_VIRTUAL_KEYS", "[]")):
        await app.state.store.create_key(spec["app"], spec["budget"], spec["tiers"], spec["key"])
    log.info("gateway started", provider=s.provider, tiers={n: t.deployment for n, t in routing().tiers.items()})
    yield
    await app.state.store.pool.close()
    await app.state.cache.aclose()


app = FastAPI(title="LLM gateway", lifespan=lifespan)
telemetry.instrument_app(app)


@app.exception_handler(HTTPException)
async def problem(_: Request, e: HTTPException):
    return JSONResponse({"title": e.detail, "status": e.status_code}, status_code=e.status_code,
                        media_type="application/problem+json")


async def caller(request: Request, authorization: str | None = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing virtual key")
    row = await request.app.state.store.key(authorization[7:].strip())
    if not row:
        raise HTTPException(401, "Unknown or disabled virtual key")
    # Fixed-window rate limit per app.
    window = f"rl:{row['app']}:{int(time.time() // 60)}"
    count = await request.app.state.cache.incr(window)
    if count == 1:
        await request.app.state.cache.expire(window, 70)
    if count > settings().requests_per_minute:
        raise HTTPException(429, "Rate limit exceeded for this application")
    if await request.app.state.store.month_spend(row["app"]) >= float(row["monthly_budget_usd"]):
        raise HTTPException(429, f"Monthly budget of ${row['monthly_budget_usd']} reached for {row['app']}")
    return dict(row)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/v1/chat/completions", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, key: dict = Depends(caller)):
    started = time.perf_counter()
    store: Store = request.app.state.store

    # Personal data never leaves the gateway.
    messages, redactions = [], set()
    for m in req.messages:
        text, found = pii.redact(m.content)
        redactions.update(found)
        messages.append({"role": m.role, "content": text})
    if redactions and settings().pii_mode == "block":
        raise HTTPException(422, f"Request contains personal data ({', '.join(sorted(redactions))})")

    tiers, reason = plan(req.task, req.model, req.latency_budget_ms)
    tiers = [t for t in tiers if t.name in key["allowed_tiers"]]
    if not tiers:
        raise HTTPException(403, f"{key['app']} may not use the requested tier")

    prompt_hash = hashlib.sha256(json.dumps([messages, req.response_format], sort_keys=True).encode()).hexdigest()
    cacheable = req.cache and req.temperature == 0
    attempts: list[str] = []

    for i, tier in enumerate(tiers):
        cache_key = f"chat:{tier.deployment}:{req.max_tokens}:{prompt_hash}"
        if cacheable and (hit := await request.app.state.cache.get(cache_key)):
            c = json.loads(hit)
            latency = int((time.perf_counter() - started) * 1000)
            saved = tier.cost(c["prompt_tokens"], c["completion_tokens"])
            await store.record(app=key["app"], feature=req.feature, task=req.task, kind="chat", tier=tier.name,
                               deployment=tier.deployment, prompt_tokens=0, completion_tokens=0, cost_usd=0,
                               saved_usd=saved, cached=True, latency_ms=latency, outcome="ok",
                               attempts=[tier.name], prompt_sha256=prompt_hash)
            return ChatResponse(id=f"chat-{uuid.uuid4().hex[:12]}", tier=tier.name, deployment=tier.deployment,
                                content=c["content"], finish_reason=c["finish_reason"],
                                usage=Usage(prompt_tokens=c["prompt_tokens"], completion_tokens=c["completion_tokens"],
                                            cost_usd=0, cached=True, latency_ms=latency),
                                route={"reason": reason, "attempts": [tier.name]}, redactions=sorted(redactions))
        attempts.append(tier.name)
        with tracer.start_as_current_span("llm.chat") as span:
            span.set_attributes({"gen_ai.request.model": tier.deployment, "gen_ai.operation.name": "chat",
                                 "llm.tier": tier.name, "llm.task": req.task, "app": key["app"]})
            try:
                out = await request.app.state.provider.chat(tier.deployment, messages, req.response_format,
                                                            req.temperature, req.max_tokens)
            except Retryable as e:
                span.set_attribute("error.type", str(e))
                log.warning("tier failed, trying next", tier=tier.name, error=str(e))
                continue
            except ContentFiltered as e:
                span.set_attribute("error.type", "content_filter")
                await store.record(app=key["app"], feature=req.feature, task=req.task, kind="chat", tier=tier.name,
                                   deployment=tier.deployment, prompt_tokens=0, completion_tokens=0, cost_usd=0,
                                   saved_usd=0, cached=False, latency_ms=int((time.perf_counter() - started) * 1000),
                                   outcome="filtered", attempts=attempts, prompt_sha256=prompt_hash)
                raise HTTPException(422, f"Blocked by content filter: {e}") from e
            span.set_attributes({"gen_ai.usage.input_tokens": out.prompt_tokens,
                                 "gen_ai.usage.output_tokens": out.completion_tokens})
        latency = int((time.perf_counter() - started) * 1000)
        cost = tier.cost(out.prompt_tokens, out.completion_tokens)
        if cacheable and out.finish_reason == "stop":
            await request.app.state.cache.set(cache_key, json.dumps(out.__dict__), ex=settings().cache_ttl_seconds)
        await store.record(app=key["app"], feature=req.feature, task=req.task, kind="chat", tier=tier.name,
                           deployment=tier.deployment, prompt_tokens=out.prompt_tokens,
                           completion_tokens=out.completion_tokens, cost_usd=cost, saved_usd=0, cached=False,
                           latency_ms=latency, outcome="fallback" if i else "ok", attempts=attempts,
                           prompt_sha256=prompt_hash)
        return ChatResponse(id=f"chat-{uuid.uuid4().hex[:12]}", tier=tier.name, deployment=tier.deployment,
                            content=out.content, finish_reason=out.finish_reason,
                            usage=Usage(prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens,
                                        cost_usd=round(cost, 8), cached=False, latency_ms=latency),
                            route={"reason": reason, "attempts": attempts}, redactions=sorted(redactions))

    await store.record(app=key["app"], feature=req.feature, task=req.task, kind="chat", tier=None, deployment=None,
                       prompt_tokens=0, completion_tokens=0, cost_usd=0, saved_usd=0, cached=False,
                       latency_ms=int((time.perf_counter() - started) * 1000), outcome="error", attempts=attempts,
                       prompt_sha256=prompt_hash)
    raise HTTPException(503, f"All model tiers unavailable ({', '.join(attempts)})")


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def embeddings(req: EmbeddingRequest, request: Request, key: dict = Depends(caller)):
    started = time.perf_counter()
    r = routing()
    texts = [pii.redact(t)[0] for t in req.input]
    try:
        vectors, tokens = await request.app.state.provider.embed(r.embedding.deployment, texts, r.dimensions)
    except Retryable as e:
        raise HTTPException(503, f"Embedding model unavailable ({e})") from e
    latency = int((time.perf_counter() - started) * 1000)
    cost = r.embedding.cost(tokens, 0)
    await request.app.state.store.record(app=key["app"], feature=req.feature, task="embed", kind="embedding",
                                         tier="embedding", deployment=r.embedding.deployment, prompt_tokens=tokens,
                                         completion_tokens=0, cost_usd=cost, saved_usd=0, cached=False,
                                         latency_ms=latency, outcome="ok", attempts=["embedding"], prompt_sha256=None)
    return EmbeddingResponse(data=vectors, usage=Usage(prompt_tokens=tokens, completion_tokens=0,
                                                       cost_usd=round(cost, 8), cached=False, latency_ms=latency))


@app.get("/v1/usage")
async def usage(request: Request, group_by: str = Query("feature", pattern="^(app|feature|tier|task|day)$"),
                days: int = Query(30, ge=1, le=366), _: dict = Depends(caller)):
    return await request.app.state.store.usage(group_by, datetime.now(UTC) - timedelta(days=days))


@app.post("/v1/keys", status_code=201)
async def create_key(req: KeyRequest, request: Request, authorization: str | None = Header(None)):
    admin = settings().admin_token
    if not admin or authorization != f"Bearer {admin}":
        raise HTTPException(403, "Admin token required")
    raw = await request.app.state.store.create_key(req.app, req.monthly_budget_usd, req.allowed_tiers)
    return {"app": req.app, "key": raw, "note": "Shown once; store it in Key Vault."}
