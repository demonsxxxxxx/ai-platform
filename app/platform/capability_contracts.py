"""Transport schemas for capability distribution and department administration."""

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_DEPARTMENT_AUTHORITY_ID_CHARS = 160


def _assert_safe_id(value: str, field_name: str) -> str:
    if not _SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def _assert_safe_department_authority_id(value: str, field_name: str) -> str:
    if (
        not value
        or value != value.strip()
        or len(value) > _MAX_DEPARTMENT_AUTHORITY_ID_CHARS
        or "," in value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def _normalize_department_ids(values: list[str], field_name: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = _assert_safe_department_authority_id(value, field_name)
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _normalize_roles(values: list[str], field_name: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = _assert_safe_id(value.strip().casefold(), field_name)
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


class CapabilityDistributionResponse(BaseModel):
    """Authoritative tenant capability distribution projection."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    capability_kind: str
    capability_id: str
    status: Literal["active", "disabled"]
    visible_to_user: bool
    scope_mode: Literal["allowlist"]
    department_ids: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    updated_by: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class CapabilityDistributionUpdateRequest(BaseModel):
    """Strict distribution configuration accepted from AI administrators."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"] = "active"
    visible_to_user: bool = True
    scope_mode: Literal["allowlist"] = "allowlist"
    department_ids: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("department_ids")
    @classmethod
    def normalize_department_ids(cls, value: list[str], info):
        return _normalize_department_ids(value, info.field_name)

    @field_validator("allowed_roles")
    @classmethod
    def normalize_allowed_roles(cls, value: list[str], info):
        return _normalize_roles(value, info.field_name)


class CapabilityDistributionAuthorityUpdateRequest(BaseModel):
    """Distribution update whose department labels require directory proof."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"] = "active"
    visible_to_user: bool = True
    scope_mode: Literal["allowlist"] = "allowlist"
    department_ids: list[str] = Field(default_factory=list, max_length=128)
    allowed_roles: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_roles")
    @classmethod
    def normalize_allowed_roles(cls, value: list[str], info):
        return _normalize_roles(value, info.field_name)


class CapabilityDistributionToggleRequest(BaseModel):
    """Toggle request accepting the supported enablement aliases."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    active: bool | None = None
    is_active: bool | None = None

    def requested_enabled(self) -> bool | None:
        if self.enabled is not None:
            return self.enabled
        if self.active is not None:
            return self.active
        return self.is_active


class CapabilityDistributionListResponse(BaseModel):
    """Tenant-scoped list of authoritative capability distributions."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    capability_distributions: list[CapabilityDistributionResponse] = Field(default_factory=list)
    total: int = 0


class CapabilityDistributionWriteResponse(BaseModel):
    """Distribution write result with its in-transaction audit record."""

    model_config = ConfigDict(extra="forbid")

    capability_distribution: CapabilityDistributionResponse
    audit_id: str
    audit_action: Literal["capability_distribution.updated", "capability_distribution.toggled"]


class DepartmentDirectoryNodeResponse(BaseModel):
    """Department-only node safe for an administrator ACL editor."""

    model_config = ConfigDict(extra="forbid")

    directory_id: str
    authority_id: str
    name: str
    path: str
    children: list["DepartmentDirectoryNodeResponse"] = Field(default_factory=list)
    selectable: bool
    reason: Literal["duplicate_authority_id"] | None = None


class DepartmentDirectoryResponse(BaseModel):
    """Admin-only company directory projection without employee identity fields."""

    model_config = ConfigDict(extra="forbid")

    departments: list[DepartmentDirectoryNodeResponse] = Field(default_factory=list)
