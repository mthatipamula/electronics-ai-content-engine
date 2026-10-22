import os
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://gateway:gateway@localhost:5432/gateway"
    redis_url: str | None = "redis://localhost:6379/0"   # empty: in-memory cache and rate limits
    provider: str = "mock"                      # "azure" or "mock" (offline tests and CI)
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None     # local only; in Azure the managed identity is used
    azure_openai_api_version: str = "2024-10-21"
    pii_mode: str = "redact"                    # "redact" or "block"
    cache_ttl_seconds: int = 86_400
    requests_per_minute: int = 120
    request_timeout_seconds: float = 60
    admin_token: str | None = None


class Tier(BaseModel):
    name: str
    deployment: str
    input_per_1m: float
    output_per_1m: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.input_per_1m + completion_tokens * self.output_per_1m) / 1_000_000


class Routing(BaseModel):
    tiers: dict[str, Tier]
    embedding: Tier
    dimensions: int
    routes: dict[str, list[str]]
    fast_if_latency_budget_ms_below: int


@cache
def settings() -> Settings:
    return Settings()


@cache
def routing() -> Routing:
    raw = yaml.safe_load((Path(__file__).parent / "routing.yaml").read_text())

    def tier(name: str, t: dict) -> Tier:
        return Tier(name=name, deployment=os.getenv(t["deployment_env"], f"mock-{name}"),
                    input_per_1m=t["input_per_1m"], output_per_1m=t.get("output_per_1m", 0))

    tiers = {n: tier(n, t) for n, t in raw["tiers"].items()}
    # Price by the model actually deployed: if two tiers share a deployment, they share the cheaper tier's price.
    for t in tiers.values():
        same = [o for o in tiers.values() if o.deployment == t.deployment]
        cheapest = min(same, key=lambda o: o.input_per_1m)
        t.input_per_1m, t.output_per_1m = cheapest.input_per_1m, cheapest.output_per_1m
    return Routing(
        tiers=tiers,
        embedding=tier("embedding", raw["embedding"]),
        dimensions=raw["embedding"]["dimensions"],
        routes=raw["routes"],
        fast_if_latency_budget_ms_below=raw["fast_if_latency_budget_ms_below"],
    )


def plan(task: str, model: str, latency_budget_ms: int | None) -> tuple[list[Tier], str]:
    """Which tiers to try, in order, and why."""
    r = routing()
    if model in r.tiers:
        return [r.tiers[model]], f"caller pinned tier '{model}'"
    order = r.routes.get(task, r.routes["default"])
    reason = f"task '{task}' route {order}"
    if latency_budget_ms is not None and latency_budget_ms < r.fast_if_latency_budget_ms_below:
        order = ["fast"] + [t for t in order if t != "fast"]
        reason += f"; latency budget {latency_budget_ms} ms prefers fast"
    return [r.tiers[t] for t in order], reason
