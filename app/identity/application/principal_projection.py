"""Public, framework-neutral projection of authenticated identity facts."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, kw_only=True)
class PrincipalResponse:
    user_id: str
    user_name: str = ""
    display_name: str
    tenant_id: str
    department_id: str = ""
    direct_department_ids: list[str] = field(default_factory=list)
    department_ids: list[str] = field(default_factory=list)
    department_authority_status: Literal["available", "unavailable"] = "unavailable"
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    is_admin: bool = False
    source: str = ""
    authz_policy_version: int = 0
    authority_source: str = ""
    authority_checked_at: str = ""
    company_refresh_required: bool = False
