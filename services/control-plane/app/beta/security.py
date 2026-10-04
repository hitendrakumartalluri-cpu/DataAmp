from __future__ import annotations
import contextvars
import hashlib
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    tenant: str
    actor: str
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()

    @property
    def admin(self):
        return "admin" in self.roles


current_identity = contextvars.ContextVar("amp_identity", default=None)


def resolve_identity(request, default_tenant, demo_mode, api_key):
    """Tokens are server-configured; callers cannot assert their own group membership."""
    configured = json.loads(os.getenv("AMP_IDENTITIES_JSON") or "{}")
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    hashed = hashlib.sha256(token.encode()).hexdigest()
    if configured:
        record = configured.get(hashed)
        if not record:
            raise PermissionError("valid bearer identity required")
        return Identity(record["tenant"], record["actor"], tuple(record.get("roles", [])), tuple(record.get("groups", [])))
    if api_key:
        if request.headers.get("x-api-key") != api_key:
            raise PermissionError("valid API key required")
        return Identity(default_tenant, "api-operator", ("admin",))
    if demo_mode:
        return Identity(default_tenant, "demo-operator", ("admin",))
    raise PermissionError("configure AMP_IDENTITIES_JSON or AMP_API_KEY")
