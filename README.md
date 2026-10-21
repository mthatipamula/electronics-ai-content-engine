# electronics-ai-content-engine

An agentic GenAI engine that writes product content for an electronics catalog (connectors, cable assemblies,
antennas, network cables), grounded in product data and datasheets, checked by guardrails and an LLM judge, and
approved by a human before it is saved. Built with Python, FastAPI, LangGraph, MCP and Azure OpenAI.

> Independent portfolio project. The catalog, brands and SKUs are fictional.

## Architecture

```
 reviewer / API client
        │  POST /api/jobs  (scoped bearer token)
        ▼
 content-engine (FastAPI + LangGraph) ── queue: Redis Streams locally / Azure Service Bus in Azure
   load_product → retrieve → generate → guardrails → judge → human review → save draft
        │ MCP (streamable HTTP, scoped tokens)          │ HTTPS (virtual key)
        ▼                                               ▼
 catalog-mcp (MCP server)                         llm-gateway (FastAPI)
   search_products, get_product,                    routing by task (fast / quality tier, fallback),
   search_datasheet (hybrid RAG: pgvector + full-     per-app keys, budgets, rate limits, PII redaction,
   text, reciprocal rank fusion), save_draft          response cache, usage and cost ledger
        │                                               │
        ▼                                               ▼
 PostgreSQL + pgvector                            Azure OpenAI (gpt-4.1-mini, text-embedding-3-small)

 OpenTelemetry traces from every service → Arize Phoenix
```

## What it demonstrates

| Area | How |
|---|---|
| Agent workflow | LangGraph state machine with retries driven by validation feedback; the model never chooses tool calls, so poisoned content can't trigger actions |
| Tool integration | MCP server with least-privilege scopes: `catalog:read` for everyone, `drafts:write` only for the engine, and no tool can change product master data |
| LLM gateway | Task-based routing (quality tier for first drafts and judging, fast tier for small fixes and tight latency budgets), fallback on throttling, per-app virtual keys and monthly budgets, cost attributed per feature |
| Grounding | Hybrid retrieval over datasheets; every number and unit in the copy must appear in the source data |
| Guardrails | Strict structured output (Pydantic), channel rules, forbidden claims, PII detection and redaction, instruction-leak checks, Azure content-filter handling |
| Prompt-injection defense | Supplier text is delimited and treated as untrusted data; chunks with injected instructions are withheld before generation (one demo datasheet is deliberately poisoned) |
| Evaluation | LLM-as-judge faithfulness scoring per draft; judge-only objections go to a human, flagged, because judges make mistakes too |
| Human in the loop | Approve, edit or reject with a reason; rejection comments become guidance for future drafts in that product category |
| Operations | Idempotent queue consumers, at-least-once delivery, OpenTelemetry traces, passwordless Azure auth (Managed Identity) in the code paths |

## Run it locally

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
./scripts/init-env.sh          # random local secrets into .env (never printed)
docker compose up -d --build   # postgres+pgvector, redis, phoenix, gateway, catalog MCP, engine
uv sync && make test           # unit tests
uv run --no-sync python scripts/smoke.py   # end-to-end checks against the running stack
```

It runs with a deterministic mock model by default (`LLM_PROVIDER=mock`). To use Azure OpenAI, deploy a chat model
and `text-embedding-3-small` in Azure AI Foundry, then set `LLM_PROVIDER=azure`, the endpoint, key and deployment
names in `.env`. The catalog re-embeds automatically when the embedding model changes.

| URL | |
|---|---|
| http://localhost:8002/docs | Content engine API |
| http://localhost:8000/docs | LLM gateway API |
| http://localhost:6006 | Phoenix traces |

## Results on Azure OpenAI (gpt-4.1-mini)

14 jobs across 7 products and two channels: 13 passed every check on the first attempt, faithfulness 1.0, about
$0.002 per product.

## Roadmap

React review UI, evaluation pipeline with a gold dataset and CI regression thresholds, Bicep for Azure Container Apps
with Managed Identity and Key Vault, GitHub Actions with Argo CD.
