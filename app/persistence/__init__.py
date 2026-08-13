"""Compatibility entry points for persistence-domain exceptions."""

from app.platform.postgres.errors import RepositoryNotFoundError

__all__ = ["RepositoryNotFoundError"]
