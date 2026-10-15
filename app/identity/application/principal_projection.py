"""Public, framework-neutral projection of authenticated identity facts."""

from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class PrincipalResponse:
    user_id: str
    user_name: str = ""
    display_name: str
    tenant_id: str
    department_id: str = ""
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    is_admin: bool = False
    source: str = ""
    authz_policy_version: int = 0
    authority_source: str = ""
    authority_checked_at: str = ""
    company_refresh_required: bool = False
