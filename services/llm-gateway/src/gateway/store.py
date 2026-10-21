"""Virtual keys and the usage ledger (Postgres). Prompts and outputs are never stored, only metadata."""

import hashlib
import secrets
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS virtual_keys (
    key_hash           TEXT PRIMARY KEY,
    app                TEXT NOT NULL UNIQUE,
    monthly_budget_usd NUMERIC(12, 4) NOT NULL,
    allowed_tiers      TEXT[] NOT NULL,
    active             BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS usage_ledger (
    id                BIGSERIAL PRIMARY KEY,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    app               TEXT NOT NULL,
    feature           TEXT NOT NULL,
    task              TEXT NOT NULL,
    kind              TEXT NOT NULL,              -- chat | embedding
    tier              TEXT,
    deployment        TEXT,
    prompt_tokens     INT NOT NULL,
    completion_tokens INT NOT NULL,
    cost_usd          NUMERIC(14, 8) NOT NULL,
    saved_usd         NUMERIC(14, 8) NOT NULL DEFAULT 0,   -- cost avoided by a cache hit
    cached            BOOLEAN NOT NULL,
    latency_ms        INT NOT NULL,
    outcome           TEXT NOT NULL,              -- ok | fallback | error
    attempts          TEXT[] NOT NULL,
    prompt_sha256     TEXT
);
CREATE INDEX IF NOT EXISTS usage_ledger_app_at ON usage_ledger (app, at);
"""

_GROUPS = {"app": "app", "feature": "feature", "tier": "tier", "task": "task", "day": "date_trunc('day', at)::date"}


class Store:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Store":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        async with pool.acquire() as c:
            await c.execute(SCHEMA)
        return cls(pool)

    async def key(self, raw: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM virtual_keys WHERE key_hash = $1 AND active",
                                        hashlib.sha256(raw.encode()).hexdigest())

    async def create_key(self, app: str, budget: float, tiers: list[str], raw: str | None = None) -> str:
        raw = raw or "vk-" + secrets.token_urlsafe(24)
        await self.pool.execute(
            """INSERT INTO virtual_keys (key_hash, app, monthly_budget_usd, allowed_tiers) VALUES ($1, $2, $3, $4)
               ON CONFLICT (app) DO UPDATE SET key_hash = EXCLUDED.key_hash,
                 monthly_budget_usd = EXCLUDED.monthly_budget_usd, allowed_tiers = EXCLUDED.allowed_tiers""",
            hashlib.sha256(raw.encode()).hexdigest(), app, budget, tiers)
        return raw

    async def month_spend(self, app: str) -> float:
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        v = await self.pool.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger WHERE app = $1 AND at >= $2", app, start)
        return float(v)

    async def record(self, **row) -> None:
        cols = ", ".join(row)
        vals = ", ".join(f"${i}" for i in range(1, len(row) + 1))
        await self.pool.execute(f"INSERT INTO usage_ledger ({cols}) VALUES ({vals})", *row.values())  # noqa: S608

    async def usage(self, group_by: str, since: datetime) -> list[dict]:
        column = _GROUPS[group_by]  # allow-listed, so safe to interpolate
        rows = await self.pool.fetch(
            f"""SELECT {column} AS key, COUNT(*) AS requests, SUM(prompt_tokens) AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens, SUM(cost_usd) AS cost_usd,
                       SUM(saved_usd) AS saved_usd, AVG(CASE WHEN cached THEN 1.0 ELSE 0 END) AS cache_hit_rate,
                       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
                       SUM(CASE WHEN outcome = 'fallback' THEN 1 ELSE 0 END) AS fallbacks
                FROM usage_ledger WHERE at >= $1 GROUP BY 1 ORDER BY cost_usd DESC""",  # noqa: S608
            since)
        return [{k: float(v) if isinstance(v, Decimal) else v for k, v in dict(r).items()} for r in rows]
