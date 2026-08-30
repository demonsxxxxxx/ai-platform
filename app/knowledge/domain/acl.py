"""Pure Knowledge ACL evaluation and Agent-scope containment."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Literal

from .connection import KnowledgeError

KnowledgeVisibility = Literal["enterprise", "restricted"]
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


def canonical_knowledge_source_id(value: str) -> str:
    candidate = value.strip()
    if not _SAFE_ID_PATTERN.fullmatch(candidate):
        raise KnowledgeError("knowledge_builder_selection_invalid")
    return candidate


def canonical_knowledge_role_id(value: str) -> str:
    candidate = value.strip().casefold()
    if not _SAFE_ID_PATTERN.fullmatch(candidate):
        raise KnowledgeError("knowledge_source_acl_identity_invalid")
    return candidate


def canonical_knowledge_user_id(value: str) -> str:
    candidate = value.strip()
    if not _SAFE_USER_ID_PATTERN.fullmatch(candidate) or ".." in candidate:
        raise KnowledgeError("knowledge_source_acl_identity_invalid")
    return candidate


def _normalized(values: Iterable[str]) -> frozenset[str]:
    return frozenset(value.strip() for value in values if value.strip())


def _normalized_roles(values: Iterable[str]) -> frozenset[str]:
    return frozenset(value.strip().casefold() for value in values if value.strip())


@dataclass(frozen=True)
class KnowledgeAcl:
    """Canonical visibility predicate shared by Builder and Run admission."""

    visibility: KnowledgeVisibility
    department_ids: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    user_ids: frozenset[str] = frozenset()

    @classmethod
    def create(
        cls,
        *,
        visibility: str,
        department_ids: Iterable[str] = (),
        roles: Iterable[str] = (),
        user_ids: Iterable[str] = (),
    ) -> KnowledgeAcl:
        normalized_visibility = (
            "enterprise" if visibility in {"enterprise", "tenant"} else visibility
        )
        if normalized_visibility not in {"enterprise", "restricted"}:
            raise ValueError("knowledge_acl_visibility_invalid")
        return cls(
            visibility=normalized_visibility,
            department_ids=_normalized(department_ids),
            roles=_normalized_roles(roles),
            user_ids=_normalized(user_ids),
        )

    def allows(
        self,
        *,
        user_id: str,
        department_id: str,
        roles: Iterable[str],
        is_admin: bool = False,
    ) -> bool:
        if is_admin or self.visibility == "enterprise":
            return True
        if user_id in self.user_ids:
            return True
        if self.department_ids and department_id not in self.department_ids:
            return False
        principal_roles = set(_normalized_roles(roles))
        if self.roles and not self.roles.intersection(principal_roles):
            return False
        return bool(self.department_ids or self.roles)

    def contains(self, narrower: KnowledgeAcl) -> bool:
        """Return whether every principal allowed by ``narrower`` is allowed here."""

        if self.visibility == "enterprise":
            return True
        if narrower.visibility == "enterprise":
            return False
        if not narrower.user_ids.issubset(self.user_ids):
            return False
        if self.department_ids:
            if not narrower.department_ids:
                return False
            if not narrower.department_ids.issubset(self.department_ids):
                return False
        if self.roles:
            if not narrower.roles:
                return False
            if not narrower.roles.issubset(self.roles):
                return False
        return bool(self.department_ids or self.roles or self.user_ids)
