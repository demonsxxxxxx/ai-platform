"""Authoritative Agent Profile lifecycle and Agent Conversation module."""

from importlib import import_module
from typing import Any

__all__ = [
    "AgentProfileAdmission",
    "AgentProfileAuthority",
    "conversation_identity_projection",
    "profile_acl_allows",
    "profile_public_projection",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    authority = import_module("app.agent_apps.authority")
    value = getattr(authority, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
