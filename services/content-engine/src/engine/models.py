"""Structured outputs. The model must return exactly these shapes; anything else is rejected and retried."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["web", "marketplace"]
Tone = Literal["technical", "concise", "friendly"]

CHANNEL_RULES: dict[str, dict] = {
    "web": {"title_max": 120, "bullets": (4, 6), "long_max": 1200},
    "marketplace": {"title_max": 80, "bullets": (5, 5), "long_max": 800},
}


class ProductContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=10, max_length=150)
    short_description: str = Field(min_length=20, max_length=300)
    long_description: str = Field(min_length=50, max_length=2000)
    bullets: list[str] = Field(min_length=3, max_length=6)
    seo_keywords: list[str] = Field(min_length=1, max_length=10)
    cited_spec_keys: list[str] = Field(description="Spec keys the copy relies on", default_factory=list)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    supported: bool
    evidence: str = Field(description="Spec key or quoted passage that supports it, or why it is unsupported")


class FaithfulnessVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[Claim]
    unsupported_count: int
    score: float = Field(ge=0, le=1, description="Supported claims / all claims")
    explanation: str


def json_schema(model: type[BaseModel], name: str) -> dict:
    """OpenAI structured-outputs format: strict schema, every property required, no extras."""
    schema = model.model_json_schema()

    def strict(node: dict) -> dict:
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                strict(child)
        if "items" in node:
            strict(node["items"])
        # Not accepted by strict structured outputs; Pydantic enforces them when the response is validated.
        for k in ("default", "title", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"):
            node.pop(k, None)
        return node

    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": strict(schema)}}
