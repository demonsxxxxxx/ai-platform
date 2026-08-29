"""Owned context product modules."""

from importlib import import_module
from typing import Any

__all__ = [
    "ContextRetrieval",
    "ContextRetrievalAuthority",
    "ContextRetrievalDenied",
    "ContextRetrievalIdentity",
    "ContextRetrievalInputError",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    retrieval = import_module("app.context.retrieval")
    value = getattr(retrieval, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
