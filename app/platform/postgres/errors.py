"""Shared PostgreSQL persistence errors."""


class RepositoryConflictError(ValueError):
    """Signal a persistence invariant or optimistic-fence conflict."""

    pass
