"""Shared persistence-domain exceptions and package entry points."""


class RepositoryNotFoundError(ValueError):
    """Signal that a requested persistence record does not exist."""

    pass
