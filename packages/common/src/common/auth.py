"""Scoped bearer tokens between services.

Locally, tokens are static values from .env mapped to scopes. In Azure the same check runs on Entra ID tokens
issued to each service's managed identity (see infra/README.md); callers never hold long-lived secrets there.
"""

import hashlib
import hmac
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    name: str
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def _registry() -> dict[str, Principal]:
    """SERVICE_TOKENS='{"<client name>": {"token": "...", "scopes": ["catalog:read"]}}'"""
    raw = json.loads(os.getenv("SERVICE_TOKENS", "{}"))
    return {
        hashlib.sha256(v["token"].encode()).hexdigest(): Principal(name, frozenset(v["scopes"]))
        for name, v in raw.items()
    }


_REGISTRY: dict[str, Principal] | None = None


def authenticate(authorization: str | None) -> Principal | None:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _registry()
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    digest = hashlib.sha256(authorization[7:].strip().encode()).hexdigest()
    for known, principal in _REGISTRY.items():
        if hmac.compare_digest(known, digest):
            return principal
    return None
