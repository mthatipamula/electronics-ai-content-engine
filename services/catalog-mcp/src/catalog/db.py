"""Catalog storage: products, datasheet chunks for hybrid retrieval, and content drafts."""

import json
import re

import asyncpg
import httpx
from common.telemetry import log
from pgvector.asyncpg import register_vector

from .seed_data import products

DIMENSIONS = 1536

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS products (
    sku        TEXT PRIMARY KEY,
    brand      TEXT NOT NULL,
    category   TEXT NOT NULL,
    name       TEXT NOT NULL,
    specs      JSONB NOT NULL,
    datasheet  TEXT NOT NULL,
    search     TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', name || ' ' || category || ' ' || brand)) STORED
);
CREATE INDEX IF NOT EXISTS products_search ON products USING gin (search);
CREATE TABLE IF NOT EXISTS datasheet_chunks (
    id         BIGSERIAL PRIMARY KEY,
    sku        TEXT NOT NULL REFERENCES products (sku),
    ordinal    INT NOT NULL,
    text       TEXT NOT NULL,
    flagged    TEXT,                      -- why the prompt-injection scanner flagged it, if it did
    embedding  vector({DIMENSIONS}) NOT NULL,
    tsv        TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (sku, ordinal)
);
ALTER TABLE datasheet_chunks ADD COLUMN IF NOT EXISTS embedded_with TEXT;
CREATE INDEX IF NOT EXISTS chunks_tsv ON datasheet_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding ON datasheet_chunks USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS content_drafts (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT NOT NULL REFERENCES products (sku),
    version     INT NOT NULL,
    content     JSONB NOT NULL,
    job_id      TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    saved_by    TEXT NOT NULL,              -- the service principal that called the tool
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sku, version)
);
"""

# Instructions aimed at a model have no place in a supplier datasheet. Matches are excluded from generation.
INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)"
    r"|(note|message|instructions?)\s+to\s+(ai|the\s+model|assistants?|llms?)"
    r"|\byou\s+are\s+now\b|\bsystem\s+prompt\b|call\s+(the\s+)?\w+\s+(tool|function)|\bcall\s+save_draft\b",
    re.I)


def chunk(text: str, max_chars: int = 700) -> list[str]:
    """Paragraph-aware chunks; paragraphs are merged up to max_chars so each chunk keeps its context."""
    out, cur = [], ""
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if cur and len(cur) + len(para) > max_chars:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    return out + ([cur] if cur else [])


async def connect(dsn: str) -> asyncpg.Pool:
    async def init(conn):
        await register_vector(conn)

    bootstrap = await asyncpg.connect(dsn)
    await bootstrap.execute(SCHEMA)
    await bootstrap.close()
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=init)


async def seed(pool: asyncpg.Pool, gateway_url: str, gateway_key: str, embedded_with: str) -> None:
    """Brings the catalog in line with the seed data: upserts products, removes stale ones nothing references,
    and embeds datasheets that have no vectors yet. Vectors from a different embedding model are not comparable,
    so a model change re-embeds everything."""
    rows = products()
    async with pool.acquire() as c, c.transaction():
        await c.executemany(
            "INSERT INTO products (sku, brand, category, name, specs, datasheet) VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (sku) DO UPDATE SET brand = EXCLUDED.brand, category = EXCLUDED.category, "
            "name = EXCLUDED.name, specs = EXCLUDED.specs, datasheet = EXCLUDED.datasheet",
            [(p["sku"], p["brand"], p["category"], p["name"], json.dumps(p["specs"]), p["datasheet"]) for p in rows])
        skus = [p["sku"] for p in rows]
        await c.execute("DELETE FROM datasheet_chunks WHERE sku <> ALL($1::text[])", skus)
        await c.execute("DELETE FROM products p WHERE sku <> ALL($1::text[]) AND NOT EXISTS "
                        "(SELECT 1 FROM content_drafts d WHERE d.sku = p.sku)", skus)
        stale = await c.fetchval("SELECT count(*) FROM datasheet_chunks WHERE embedded_with IS DISTINCT FROM $1",
                                 embedded_with)
        if stale:
            log.info("embedding model changed; re-embedding datasheets", now=embedded_with)
            await c.execute("DELETE FROM datasheet_chunks")
        done = {r["sku"] for r in await c.fetch("SELECT DISTINCT sku FROM datasheet_chunks")}
    pieces = [(p["sku"], i, t) for p in rows if p["sku"] not in done for i, t in enumerate(chunk(p["datasheet"]))]
    if not pieces:
        return
    async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {gateway_key}"}) as http:
        for start in range(0, len(pieces), 64):
            batch = pieces[start:start + 64]
            r = await http.post(f"{gateway_url}/v1/embeddings",
                                json={"input": [t for _, _, t in batch], "feature": "catalog-ingest"})
            r.raise_for_status()
            async with pool.acquire() as c:
                await c.executemany(
                    "INSERT INTO datasheet_chunks (sku, ordinal, text, flagged, embedding, embedded_with) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    [(sku, i, t, _flag(t), vec, embedded_with)
                     for (sku, i, t), vec in zip(batch, r.json()["data"], strict=True)])
    log.info("catalog seeded", products=len(rows), embedded_chunks=len(pieces))


def _flag(text: str) -> str | None:
    m = INJECTION.search(text)
    return f"possible prompt injection: '{m.group(0)}'" if m else None
