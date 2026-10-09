"""Fail-closed identity for untrusted authorized Skill catalogs."""


class AuthorizedSkillCatalogError(ValueError):
    """An authorized Skill catalog cannot be trusted or materialized."""
