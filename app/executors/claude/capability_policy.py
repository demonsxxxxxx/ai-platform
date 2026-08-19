"""Pure capability planning and parameter policy for the Claude executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from app.required_tool_contract import RequiredCapabilityDeclaration
from app.tool_policy import evaluate_tool_policy

_SDK_INTERNAL_CONTEXT_TOOLS = (
    "read_session_messages",
    "read_context_file",
    "read_run_artifact",
    "stage_context_file_to_workspace",
    "stage_run_artifact_to_workspace",
    "search_memory",
)
_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX = "mcp__ai-platform-context__"
_SDK_INTERNAL_CONTEXT_PARAMETER_KEYS = {
    "read_session_messages": ("limit", "offset", "max_tokens"),
    "read_context_file": ("file_id", "max_bytes"),
    "read_run_artifact": ("artifact_id", "max_bytes"),
    "stage_context_file_to_workspace": ("file_id", "max_bytes"),
    "stage_run_artifact_to_workspace": ("artifact_id", "max_bytes"),
    "search_memory": ("query", "limit", "max_tokens"),
}
_SDK_INTERNAL_CONTEXT_REQUIRED_PARAMETER_KEYS = {
    "read_context_file": ("file_id",),
    "read_run_artifact": ("artifact_id",),
    "stage_context_file_to_workspace": ("file_id",),
    "stage_run_artifact_to_workspace": ("artifact_id",),
}
_BUILTIN_PARAMETER_KEYS = {
    "Read": ("file_path",),
    "Glob": ("pattern", "path"),
    "LS": ("path",),
    "Bash": ("command",),
    "Write": ("file_path", "content"),
    "Edit": ("file_path", "old_string", "new_string", "replace_all"),
    "NotebookEdit": (
        "notebook_path",
        "new_source",
        "cell_id",
        "cell_type",
        "edit_mode",
    ),
    "Agent": ("agent", "prompt", "description"),
    "WebFetch": ("url", "prompt"),
    "WebSearch": ("query",),
    "Skill": ("skill",),
}
_BUILTIN_REQUIRED_PARAMETER_KEYS = {
    "Bash": ("command",),
    "Write": ("file_path", "content"),
    "Skill": ("skill",),
}


def _canonical_tool_policy_subjects(value: object) -> dict[str, dict[str, Any]]:
    """Keep only exact, complete capability subjects authorized by the worker."""

    if not isinstance(value, list):
        return {}
    subjects: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        identity = str(raw.get("identity") or "")
        if identity.startswith("mcp__"):
            server_id = raw.get("mcp_server")
            tool_name = raw.get("mcp_tool")
            if server_id == "ai-platform-context":
                internal_tool = identity.removeprefix(
                    _SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX
                )
                if (
                    not identity.startswith(_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX)
                    or internal_tool not in _SDK_INTERNAL_CONTEXT_TOOLS
                ):
                    continue
                tool_name = internal_tool
            if (
                not isinstance(server_id, str)
                or not server_id
                or not isinstance(tool_name, str)
                or not tool_name
                or identity != f"mcp__{server_id}__{tool_name}"
            ):
                continue
        validation = evaluate_tool_policy(
            tool={
                "requested_identity": identity,
                "declared_identities": [identity],
                "registered": raw.get("registered"),
                "declared": raw.get("declared"),
                "active": raw.get("active"),
                "distributed": raw.get("distributed"),
                "identity_authorized": raw.get("identity_authorized"),
                "object_authorized": raw.get("object_authorized"),
                "parameters_authorized": raw.get("parameters_authorized"),
                "risk_level": raw.get("risk_level"),
                "write_capable": raw.get("write_capable"),
            }
        )
        if (
            not validation.allowed
            or validation.canonical_identity != identity
            or identity in subjects
        ):
            continue
        subject = dict(raw)
        subject["identity"] = identity
        subjects[identity] = subject
    return subjects


@dataclass(frozen=True)
class CapabilityExecutionPlan:
    """Separate available capabilities from explicit execution requirements."""

    available: frozenset[tuple[str, str]]
    required: tuple[RequiredCapabilityDeclaration, ...]

    @classmethod
    def from_tool_policy_subjects(
        cls,
        value: object,
        *,
        required_skill_identity: str | None = None,
        available_skill_identities: object = (),
        registered_mcp_servers: dict[str, object] | None = None,
    ) -> "CapabilityExecutionPlan":
        """Build one executor-private plan from exact server-authorized subjects."""

        available: set[tuple[str, str]] = set()
        for identity, subject in _canonical_tool_policy_subjects(value).items():
            server_id = subject.get("mcp_server")
            tool_name = subject.get("mcp_tool")
            if (
                not isinstance(server_id, str)
                or not server_id
                or server_id == "ai-platform-context"
                or not isinstance(tool_name, str)
                or not tool_name
                or identity != f"mcp__{server_id}__{tool_name}"
                or (
                    registered_mcp_servers is not None
                    and server_id not in registered_mcp_servers
                )
            ):
                continue
            available.add(("mcp", identity))
        if isinstance(available_skill_identities, (list, tuple, set, frozenset)):
            for identity in available_skill_identities:
                if isinstance(identity, str) and identity:
                    available.add(("skill", identity))
        required: tuple[RequiredCapabilityDeclaration, ...] = ()
        if required_skill_identity:
            declaration = RequiredCapabilityDeclaration.from_authorized_subject(
                capability_kind="skill",
                canonical_identity=required_skill_identity,
            )
            required = (declaration,)
            available.add(("skill", required_skill_identity))
        return cls(available=frozenset(available), required=required)

    @classmethod
    def mcp_requires_sandbox(
        cls,
        value: object,
        *,
        broker_capability: object = None,
    ) -> bool:
        """Require brokered execution for issued or policy-authorized MCP access."""

        plan = cls.from_tool_policy_subjects(value)
        return bool(broker_capability) or any(
            kind == "mcp" for kind, _identity in plan.available
        )


def internal_context_tool_policy_subjects(tool_names: object) -> list[dict[str, Any]]:
    """Build exact broker subjects for explicitly selected scoped context tools."""

    if not isinstance(tool_names, list | tuple | set | frozenset):
        return []
    selected = {
        str(tool_name)
        for tool_name in tool_names
        if isinstance(tool_name, str) and tool_name in _SDK_INTERNAL_CONTEXT_TOOLS
    }
    subjects: list[dict[str, Any]] = []
    for tool_name in _SDK_INTERNAL_CONTEXT_TOOLS:
        if tool_name not in selected:
            continue
        identity = f"{_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX}{tool_name}"
        subjects.append(
            {
                "identity": identity,
                "mcp_server": "ai-platform-context",
                "registered": True,
                "declared": True,
                "active": True,
                "distributed": True,
                "identity_authorized": True,
                "object_authorized": True,
                "parameters_authorized": True,
                "risk_level": "medium" if tool_name.startswith("stage_") else "low",
                "write_capable": tool_name.startswith("stage_"),
                "allowed_parameter_keys": list(
                    _SDK_INTERNAL_CONTEXT_PARAMETER_KEYS[tool_name]
                ),
                "required_parameter_keys": list(
                    _SDK_INTERNAL_CONTEXT_REQUIRED_PARAMETER_KEYS.get(tool_name, ())
                ),
            }
        )
    return subjects


def _normalized_key(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum()).lower()


def _append_skill_candidate(candidates: list[str], value: object) -> None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            candidates.append(candidate)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in {
                "skill",
                "skillid",
                "skillname",
                "name",
                "id",
                "selectedskill",
                "selectedskillid",
                "selectedskillname",
            }:
                _append_skill_candidate(candidates, item)
        return
    if isinstance(value, list):
        for item in value:
            _append_skill_candidate(candidates, item)


def _extract_skill_names_from_tool_input(
    tool_input: Any,
    allowed_skill_names: set[str],
) -> list[str]:
    candidates: list[str] = []
    _append_skill_candidate(candidates, tool_input)
    names: list[str] = []
    for candidate in candidates:
        if candidate not in allowed_skill_names:
            continue
        if candidate not in names:
            names.append(candidate)
    return names


def _authorized_parameter_keys(
    subject: dict[str, Any],
    tool_name: str,
) -> set[str]:
    configured = subject.get("allowed_parameter_keys")
    if isinstance(configured, list) and all(
        isinstance(item, str) and item for item in configured
    ):
        return set(configured)
    return set(_BUILTIN_PARAMETER_KEYS.get(tool_name, ()))


def _delegates_external_mcp_parameters(
    subject: dict[str, Any],
    tool_name: str,
) -> bool:
    identity = subject.get("identity")
    server_id = subject.get("mcp_server")
    mcp_tool = subject.get("mcp_tool")
    return (
        subject.get("parameter_delegation") == "external_mcp"
        and isinstance(identity, str)
        and isinstance(server_id, str)
        and bool(server_id)
        and server_id != "ai-platform-context"
        and isinstance(mcp_tool, str)
        and bool(mcp_tool)
        and identity == f"mcp__{server_id}__{mcp_tool}"
        and tool_name == identity
    )


def _parameters_match_subject(
    subject: dict[str, Any],
    tool_name: str,
    tool_input: object,
) -> bool:
    if not isinstance(tool_input, dict):
        return False
    schema = subject.get("mcp_tool_schema")
    schema_authoritative = isinstance(schema, dict)
    delegates_external_mcp = _delegates_external_mcp_parameters(
        subject,
        tool_name,
    )
    schema_properties = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(schema_properties, dict):
        if schema.get("additionalProperties") is False:
            allowed_keys = {
                str(key) for key in schema_properties if isinstance(key, str) and key
            }
        else:
            allowed_keys = set(tool_input)
    elif isinstance(schema, dict) and schema.get("additionalProperties") is False:
        allowed_keys = set()
    elif isinstance(schema, dict):
        # The registered MCP remains the schema authority when JSON Schema
        # does not enumerate properties; relay scope still fixes the identity.
        allowed_keys = set(tool_input)
    elif delegates_external_mcp:
        # The capability subject fixes identity; the relay's live MCP schema
        # remains authoritative for the external tool's parameter object.
        allowed_keys = set(tool_input)
    else:
        allowed_keys = _authorized_parameter_keys(subject, tool_name)
    if (
        not allowed_keys
        and not schema_authoritative
        and not delegates_external_mcp
    ) or not set(tool_input).issubset(allowed_keys):
        return False
    required = subject.get("required_parameter_keys")
    if required is None and isinstance(schema, dict):
        required = schema.get("required", ())
    if required is None:
        required = list(_BUILTIN_REQUIRED_PARAMETER_KEYS.get(tool_name, ()))
    if isinstance(required, list):
        if not all(isinstance(key, str) and key for key in required):
            return False
        if any(
            key not in tool_input or tool_input[key] in (None, "") for key in required
        ):
            return False
    elif tool_name == "Bash":
        if (
            not isinstance(tool_input.get("command"), str)
            or not tool_input["command"].strip()
        ):
            return False
    if tool_name == "Skill":
        allowed_skill_names = subject.get("allowed_skill_names")
        requested = _extract_skill_names_from_tool_input(
            tool_input, set(allowed_skill_names or [])
        )
        if not requested:
            return False
    expected_objects = subject.get("object_constraints")
    return not isinstance(expected_objects, dict) or not any(
        tool_input.get(key) != value for key, value in expected_objects.items()
    )


def _dynamic_mcp_server_option(
    *, relay_url: str, capability: str, server_id: str
) -> dict[str, Any]:
    """Register one capability-bound relay URL without exposing MCP secrets."""

    parsed = urlsplit(str(relay_url or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not str(capability or "").startswith("mcpbrk:")
        or not server_id
    ):
        raise ValueError("dynamic MCP relay registration is invalid")
    normalized_path = (parsed.path or "/").rstrip("/")
    return {
        "type": "http",
        "url": urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                f"{normalized_path}/{quote(server_id, safe='')}",
                "",
                "",
            )
        ),
        "headers": {"X-MCP-Broker-Capability": capability},
    }


def _dynamic_mcp_server_options(
    subjects: dict[str, dict[str, Any]],
    *,
    relay_url: str,
    capability: str,
) -> dict[str, dict[str, Any]]:
    server_ids = {
        str(subject.get("mcp_server") or "")
        for identity, subject in subjects.items()
        if identity.startswith("mcp__")
        and str(subject.get("mcp_server") or "")
        and str(subject.get("mcp_server") or "") != "ai-platform-context"
    }
    return {
        server_id: _dynamic_mcp_server_option(
            relay_url=relay_url,
            capability=capability,
            server_id=server_id,
        )
        for server_id in sorted(server_ids)
    }
