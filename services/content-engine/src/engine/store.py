"""Jobs, their audit trail, and reviewer feedback."""

import json
from typing import Any

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    sku             TEXT NOT NULL,
    category        TEXT,
    channel         TEXT NOT NULL,
    tone            TEXT NOT NULL,
    status          TEXT NOT NULL,     -- QUEUED RUNNING PENDING_REVIEW APPROVED REJECTED FAILED
    attempts        INT NOT NULL DEFAULT 0,
    content         JSONB,
    guardrails      JSONB,
    judge           JSONB,
    sources         JSONB,
    steps           JSONB NOT NULL DEFAULT '[]',
    model_calls     JSONB NOT NULL DEFAULT '[]',
    prompt_tokens   INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 6) NOT NULL DEFAULT 0,
    error           TEXT,
    requested_by    TEXT NOT NULL,
    reviewed_by     TEXT,
    review_comment  TEXT,
    review_category TEXT,
    final_content   JSONB,
    edited          BOOLEAN,
    draft_version   INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, created_at DESC);
CREATE TABLE IF NOT EXISTS reviewer_guidance (
    id          BIGSERIAL PRIMARY KEY,
    category    TEXT NOT NULL,           -- product category the lesson applies to
    channel     TEXT NOT NULL,
    lesson      TEXT NOT NULL,
    source_job  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

JSON_COLS = {"content", "guardrails", "judge", "sources", "steps", "model_calls", "final_content"}


def _row(r: asyncpg.Record | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    if d.get("cost_usd") is not None:
        d["cost_usd"] = float(d["cost_usd"])
    return d


class Store:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Store":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        async with pool.acquire() as c:
            await c.execute(SCHEMA)
        return cls(pool)

    async def create(self, job_id: str, sku: str, channel: str, tone: str, requested_by: str) -> None:
        await self.pool.execute("INSERT INTO jobs (id, sku, channel, tone, status, requested_by) "
                                "VALUES ($1, $2, $3, $4, 'QUEUED', $5)", job_id, sku, channel, tone, requested_by)

    async def claim(self, job_id: str) -> dict | None:
        """QUEUED -> RUNNING exactly once, so a redelivered event can't run a job twice."""
        return _row(await self.pool.fetchrow(
            "UPDATE jobs SET status = 'RUNNING', updated_at = now() WHERE id = $1 AND status = 'QUEUED' RETURNING *",
            job_id))

    async def update(self, job_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
        vals = [json.dumps(v) if k in JSON_COLS else v for k, v in fields.items()]
        await self.pool.execute(f"UPDATE jobs SET {sets}, updated_at = now() WHERE id = $1",  # noqa: S608
                                job_id, *vals)

    async def get(self, job_id: str) -> dict | None:
        return _row(await self.pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id))

    async def list_jobs(self, status: str | None, limit: int) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT id, sku, category, channel, status, attempts, cost_usd, "
            "(judge->>'score')::float AS faithfulness, COALESCE((judge->>'needs_attention')::bool, false) "
            "AS needs_attention, content->>'title' AS title, requested_by, reviewed_by, created_at, updated_at "
            "FROM jobs WHERE ($1::text IS NULL OR status = $1) ORDER BY created_at DESC LIMIT $2", status, limit)
        return [_row(r) for r in rows]

    async def decide(self, job_id: str, status: str, **fields: Any) -> dict | None:
        """PENDING_REVIEW -> APPROVED/REJECTED exactly once (two reviewers can't both decide)."""
        sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=3))
        vals = [json.dumps(v) if k in JSON_COLS else v for k, v in fields.items()]
        return _row(await self.pool.fetchrow(
            f"UPDATE jobs SET status = $2, {sets}, updated_at = now() "  # noqa: S608
            f"WHERE id = $1 AND status = 'PENDING_REVIEW' RETURNING *", job_id, status, *vals))

    async def add_guidance(self, category: str, channel: str, lesson: str, job_id: str) -> None:
        await self.pool.execute("INSERT INTO reviewer_guidance (category, channel, lesson, source_job) "
                                "VALUES ($1, $2, $3, $4)", category, channel, lesson[:500], job_id)

    async def guidance(self, category: str, channel: str, limit: int = 5) -> list[str]:
        rows = await self.pool.fetch("SELECT lesson FROM reviewer_guidance WHERE category = $1 AND channel = $2 "
                                     "ORDER BY created_at DESC LIMIT $3", category, channel, limit)
        return [r["lesson"] for r in rows]

    async def metrics(self) -> dict:
        r = await self.pool.fetchrow("""
            SELECT count(*) FILTER (WHERE status IN ('APPROVED', 'REJECTED')) AS reviewed,
                   count(*) FILTER (WHERE status = 'APPROVED') AS approved,
                   count(*) FILTER (WHERE status = 'APPROVED' AND edited) AS approved_with_edits,
                   count(*) FILTER (WHERE status = 'REJECTED') AS rejected,
                   count(*) FILTER (WHERE status = 'PENDING_REVIEW') AS pending_review,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                   avg((judge->>'score')::numeric) AS avg_faithfulness,
                   avg(attempts) AS avg_attempts,
                   avg(cost_usd) AS avg_cost_per_job,
                   sum(cost_usd) AS total_cost
            FROM jobs""")
        d = {k: (float(v) if v is not None and not isinstance(v, int) else v) for k, v in dict(r).items()}
        d["approval_rate"] = d["approved"] / d["reviewed"] if d["reviewed"] else None
        d["first_pass_acceptance"] = ((d["approved"] - d["approved_with_edits"]) / d["reviewed"]
                                      if d["reviewed"] else None)
        cats = await self.pool.fetch("SELECT review_category, count(*) FROM jobs WHERE status = 'REJECTED' "
                                     "GROUP BY 1 ORDER BY 2 DESC")
        d["rejection_reasons"] = {c["review_category"]: c["count"] for c in cats}
        return d
