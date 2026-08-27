"""Transport factory for model administration and the internal proxy."""

from .model_management import build_model_management_router

__all__ = ["build_model_management_router"]
