from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str = Field("auto", description="'auto' routes by task; or pin a tier: 'fast' / 'quality'")
    task: str = "default"
    feature: str = Field("unspecified", max_length=80, description="Business feature, for cost attribution")
    messages: list[Message] = Field(min_length=1)
    response_format: dict[str, Any] | None = None
    temperature: float = Field(0.2, ge=0, le=2)
    max_tokens: int = Field(1200, ge=1, le=16_000)
    latency_budget_ms: int | None = Field(None, ge=100)
    cache: bool = True


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cached: bool
    latency_ms: int


class ChatResponse(BaseModel):
    id: str
    tier: str
    deployment: str
    content: str
    finish_reason: str | None
    usage: Usage
    route: dict[str, Any]
    redactions: list[str] = []


class EmbeddingRequest(BaseModel):
    input: list[str] = Field(min_length=1, max_length=256)
    feature: str = "unspecified"


class EmbeddingResponse(BaseModel):
    data: list[list[float]]
    usage: Usage


class KeyRequest(BaseModel):
    app: str = Field(pattern=r"^[a-z0-9-]{3,40}$")
    monthly_budget_usd: float = Field(gt=0)
    allowed_tiers: list[str] = ["fast", "quality"]
