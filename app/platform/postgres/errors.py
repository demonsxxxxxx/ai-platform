"""Shared PostgreSQL persistence errors."""


class RepositoryConflictError(ValueError):
    """Signal a persistence invariant or optimistic-fence conflict."""

    pass


class RepositoryNotFoundError(ValueError):
    """Signal that a requested persistence record does not exist."""

    pass
