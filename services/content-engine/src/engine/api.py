"""Content engine API: request content, review it, and watch quality and cost."""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from common import auth, telemetry
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .clients import Gateway, Spend, catalog
from .events import bus
from .models import Channel, ProductContent, Tone
from .store import Store
from .workflow import build

telemetry.setup("content-engine")
log = telemetry.log
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://content:content@localhost:5432/content")
RUN_WORKER = os.getenv("RUN_WORKER", "true") == "true"


async def run(store: Store, gateway: Gateway, job_id: str, sku: str, channel: str, tone: str) -> dict:
    """Runs the workflow once and returns its final state plus spend."""
    spend = Spend()
    async with catalog() as tools:
        graph = build(gateway, tools, spend, store.guidance)
        state = await graph.ainvoke({"job_id": job_id, "sku": sku, "channel": channel, "tone": tone})
    return {"state": state, "spend": spend}


async def handle(app: FastAPI, message: dict) -> None:
    store: Store = app.state.store
    job = await store.claim(message["job_id"])
    if job is None:
        return  # already taken (redelivery) or cancelled
    log.info("job started", job_id=job["id"], sku=job["sku"])
    try:
        out = await run(store, app.state.gateway, job["id"], job["sku"], job["channel"], job["tone"])
    except Exception as e:  # noqa: BLE001 - recorded on the job for the reviewer
        log.exception("job failed", job_id=job["id"])
        await store.update(job["id"], status="FAILED", error=f"{type(e).__name__}: {e}"[:1000])
        return
    s, spend = out["state"], out["spend"]
    await store.update(
        job["id"], status=s["outcome"], attempts=s.get("attempts", 0), category=s["facts"]["category"],
        content=s.get("content"), guardrails=s.get("guard"), judge=s.get("judge"),
        sources={"passages": s.get("passages", []), "withheld": s.get("withheld", []), "facts": s["facts"],
                 "guidance": s.get("guidance", [])},
        steps=s.get("steps", []), model_calls=spend.calls, prompt_tokens=spend.prompt_tokens,
        completion_tokens=spend.completion_tokens, cost_usd=round(spend.cost_usd, 6))
    log.info("job finished", job_id=job["id"], outcome=s["outcome"], cost_usd=round(spend.cost_usd, 6))


async def consume_forever(app: FastAPI) -> None:
    """The queue consumer must never die silently: log the failure and reconnect."""
    while True:
        try:
            await app.state.bus.consume(lambda m: handle(app, m))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("job consumer crashed; restarting in 5 s")
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = await Store.connect(DATABASE_URL)
    app.state.gateway = Gateway()
    app.state.bus = bus()
    worker = asyncio.create_task(consume_forever(app)) if RUN_WORKER else None
    yield
    if worker:
        worker.cancel()
    await app.state.bus.close()
    await app.state.store.pool.close()


app = FastAPI(title="Content engine", lifespan=lifespan)
telemetry.instrument_app(app)


@app.exception_handler(HTTPException)
async def problem(_: Request, e: HTTPException):
    return JSONResponse({"title": e.detail, "status": e.status_code}, status_code=e.status_code,
                        media_type="application/problem+json")


def principal(scope: str):
    def check(authorization: str | None = Header(None)) -> auth.Principal:
        p = auth.authenticate(authorization)
        if p is None:
            raise HTTPException(401, "Sign in required")
        if not p.has(scope):
            raise HTTPException(403, f"Requires the '{scope}' permission")
        return p
    return check


class JobRequest(BaseModel):
    skus: list[str] = Field(min_length=1, max_length=50)
    channel: Channel = "web"
    tone: Tone = "technical"


class Decision(BaseModel):
    reviewer: str = Field(min_length=2, max_length=80)
    decision: Literal["approve", "reject"]
    content: ProductContent | None = Field(None, description="Edited content, if the reviewer changed it")
    category: Literal["inaccurate", "missing_info", "tone", "formatting", "compliance", "other"] | None = None
    comment: str | None = Field(None, max_length=500)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/jobs", status_code=202)
async def create_jobs(req: JobRequest, request: Request, p: auth.Principal = Depends(principal("content:request"))):
    ids = []
    for sku in dict.fromkeys(req.skus):
        job_id = f"job-{uuid.uuid4().hex[:10]}"
        await request.app.state.store.create(job_id, sku, req.channel, req.tone, p.name)
        await request.app.state.bus.publish({"job_id": job_id})
        ids.append({"job_id": job_id, "sku": sku})
    return {"jobs": ids}


@app.get("/api/jobs")
async def list_jobs(request: Request, status: str | None = None, limit: int = Query(100, le=500),
                    _: auth.Principal = Depends(principal("content:read"))):
    return await request.app.state.store.list_jobs(status, limit)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request, _: auth.Principal = Depends(principal("content:read"))):
    job = await request.app.state.store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/api/jobs/{job_id}/decision")
async def decide(job_id: str, d: Decision, request: Request,
                 p: auth.Principal = Depends(principal("content:review"))):
    store: Store = request.app.state.store
    job = await store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    reviewer = f"{d.reviewer} ({p.name})"
    if d.decision == "reject":
        if not (d.category and d.comment):
            raise HTTPException(422, "A rejection needs a category and a comment so the agent can learn from it")
        updated = await store.decide(job_id, "REJECTED", reviewed_by=reviewer, review_category=d.category,
                                     review_comment=d.comment)
        if updated is None:
            raise HTTPException(409, f"Job is {job['status']}, not awaiting review")
        await store.add_guidance(job["category"], job["channel"], d.comment, job_id)
        return updated
    final = (d.content.model_dump() if d.content else job["content"])
    edited = d.content is not None and d.content.model_dump() != job["content"]
    updated = await store.decide(job_id, "APPROVED", reviewed_by=reviewer, final_content=final, edited=edited,
                                 review_comment=d.comment)
    if updated is None:
        raise HTTPException(409, f"Job is {job['status']}, not awaiting review")
    async with catalog() as tools:
        saved = await tools.save_draft(job["sku"], final, job_id, reviewer)
    await store.update(job_id, draft_version=saved["version"])
    if edited and d.comment:
        await store.add_guidance(job["category"], job["channel"], d.comment, job_id)
    return await store.get(job_id)


@app.post("/api/generate")
async def generate_now(req: JobRequest, request: Request, _: auth.Principal = Depends(principal("content:evaluate"))):
    """Synchronous run without review or saving, for the evaluation pipeline."""
    results = []
    for sku in req.skus:
        out = await run(request.app.state.store, request.app.state.gateway, f"eval-{uuid.uuid4().hex[:8]}", sku,
                        req.channel, req.tone)
        s, spend = out["state"], out["spend"]
        results.append({"sku": sku, "outcome": s["outcome"], "attempts": s.get("attempts"),
                        "content": s.get("content"), "guardrails": s.get("guard"), "judge": s.get("judge"),
                        "facts": s["facts"], "passages": [p["text"] for p in s.get("passages", [])],
                        "withheld": s.get("withheld", []), "cost_usd": spend.cost_usd, "model_calls": spend.calls})
    return results


@app.get("/api/products")
async def products(q: str = "", category: str | None = None, _: auth.Principal = Depends(principal("content:read"))):
    async with catalog() as tools:
        return {"categories": await tools.list_categories(),
                "products": await tools.search_products(q, category, 50)}


@app.get("/api/metrics")
async def metrics(request: Request, _: auth.Principal = Depends(principal("content:read"))):
    return await request.app.state.store.metrics()


@app.get("/api/usage")
async def usage(request: Request, group_by: str = "feature", days: int = 30,
                _: auth.Principal = Depends(principal("content:read"))):
    return await request.app.state.gateway.usage(group_by, days)


@app.get("/api/evals/latest")
def latest_eval(_: auth.Principal = Depends(principal("content:read"))):
    reports = sorted(Path(os.getenv("EVAL_REPORTS", "evals/reports")).glob("*.json"))
    if not reports:
        raise HTTPException(404, "No evaluation report yet; run `make eval`")
    return JSONResponse(content=json.loads(reports[-1].read_text()))
