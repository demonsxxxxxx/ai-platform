"""Bind selected MCP credential ownership to a platform-created Sandbox lease."""

import re
from typing import Any

from app.mcp.domain.errors import McpRuntimeContextError


MCP_CREDENTIAL_SUBJECTS_LEASE_KEY = "mcp_credential_subjects"


def bound_mcp_credential_subject(value: object, *, tenant_id: str, user_id: str) -> dict[str, Any]:
    identity_keys = {"tenant_id", "user_id"}
    if not isinstance(value, dict) or value.get("tenant_id") != tenant_id or value.get("user_id") != user_id:
        raise McpRuntimeContextError("mcp_credential_binding_invalid", status_code=403)
    if set(value) == identity_keys | {"grant_id", "subject_epoch"}:
        valid = (isinstance(value["grant_id"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", value["grant_id"]) is not None
                 and type(value["subject_epoch"]) is int and value["subject_epoch"] >= 0)
    elif set(value) == identity_keys | {"legacy_jwt_id"}:
        valid = (isinstance(value["legacy_jwt_id"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", value["legacy_jwt_id"]) is not None)
    else:
        valid = False
    if not valid:
        raise McpRuntimeContextError("mcp_credential_binding_invalid", status_code=403)
    return dict(value)


def mcp_credential_subjects_for_lease(
    subjects: list[dict[str, Any]], *, tenant_id: str, user_id: str,
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for subject in subjects:
        server = subject.get("mcp_server")
        config = subject.get("mcp_server_config")
        if (not isinstance(server, str) or not server
            or server in {"ai-platform-context", "ai-platform-response"}
            or subject.get("identity") != f"mcp__{server}__{subject.get('mcp_tool')}"
            or not all(subject.get(key) is True for key in (
                "registered", "declared", "active", "distributed", "identity_authorized",
                "object_authorized", "parameters_authorized",
            ))
            or not isinstance(config, dict) or "credential_subject" not in config):
            continue
        binding = bound_mcp_credential_subject(
            config["credential_subject"], tenant_id=tenant_id, user_id=user_id,
        )
        if server in bindings and bindings[server] != binding:
            raise McpRuntimeContextError("mcp_credential_binding_invalid", status_code=403)
        bindings[server] = binding
    return bindings
