"""Pure capability planning and parameter policy for the Claude executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.required_tool_contract import RequiredCapabilityDeclaration
from app.tool_policy import evaluate_tool_policy

_SDK_INTERNAL_CONTEXT_TOOLS = (
    "read_session_messages",
    "read_run_artifact",
    "stage_context_file_to_workspace",
    "stage_run_artifact_to_workspace",
    "search_memory",
)
_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX = "mcp__ai-platform-context__"
_SDK_INTERNAL_CONTEXT_PARAMETER_KEYS = {
    "read_session_messages": ("limit", "offset", "max_tokens"),
    "read_run_artifact": ("artifact_id", "max_bytes"),
    "stage_context_file_to_workspace": ("file_id", "max_bytes"),
    "stage_run_artifact_to_workspace": ("artifact_id", "max_bytes"),
    "search_memory": ("query", "limit", "max_tokens"),
}
_SDK_INTERNAL_CONTEXT_REQUIRED_PARAMETER_KEYS = {
    "read_run_artifact": ("artifact_id",),
    "stage_context_file_to_workspace": ("file_id",),
    "stage_run_artifact_to_workspace": ("artifact_id",),
}
_BUILTIN_PARAMETER_KEYS = {
    "Read": ("file_path",),
    "Glob": ("pattern", "path"),
    "Grep": (
        "pattern",
        "path",
        "glob",
        "output_mode",
        "-i",
        "multiline",
        "head_limit",
        "offset",
        "context",
    ),
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
    "Grep": ("pattern",),
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


def _parameters_match_subject(
    subject: dict[str, Any],
    tool_name: str,
    tool_input: object,
) -> bool:
    if not isinstance(tool_input, dict):
        return False
    allowed_keys = _authorized_parameter_keys(subject, tool_name)
    if not allowed_keys or not set(tool_input).issubset(allowed_keys):
        return False
    required = subject.get(
        "required_parameter_keys",
        list(_BUILTIN_REQUIRED_PARAMETER_KEYS.get(tool_name, ())),
    )
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


def _mcp_server_options(
    subjects: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    servers: dict[str, dict[str, str]] = {}
    for identity, subject in subjects.items():
        config = subject.get("mcp_server_config")
        if not identity.startswith("mcp__") or not isinstance(config, dict):
            continue
        server_id = str(subject.get("mcp_server") or "")
        transport = str(config.get("type") or "").lower()
        endpoint = str(config.get("url") or "")
        parsed = urlsplit(endpoint)
        if (
            not server_id
            or transport not in {"http", "sse"}
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        ):
            continue
        candidate = {"type": transport, "url": endpoint}
        existing = servers.get(server_id)
        if existing is not None and existing != candidate:
            raise ValueError("conflicting MCP server registration")
        servers[server_id] = candidate
    return servers
