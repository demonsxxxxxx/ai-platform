import asyncio
import base64
import json
import os
import re
import shlex
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable
from pathlib import Path, PurePosixPath
from typing import Any

from app.context_manifest import available_context_retrieval_tools, truncate_utf8_text
from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalIdentity,
    ContextRetrievalInputError,
)
from app.control_plane_contracts import (
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    sanitize_public_payload,
    sanitize_public_text,
)
from app.executors.claude.capability_policy import (
    CapabilityExecutionPlan,
    _SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX,
    _SDK_INTERNAL_CONTEXT_TOOLS,
    _authorized_parameter_keys as _authorized_parameter_keys,
    _canonical_tool_policy_subjects,
    _extract_skill_names_from_tool_input,
    _mcp_server_options,
    _parameters_match_subject,
    internal_context_tool_policy_subjects,
)
from app.executors.claude.prompts import (
    build_skill_prompt as build_skill_prompt,
    context_pack_prompt_section as _prompt_context_pack_prompt_section,
    translation_target_language as _prompt_translation_target_language,
    with_selected_skill_invocation_requirement as _with_selected_skill_invocation_requirement,
)
from app.execution.api import ClaudeSdkAgentEventAdapter
from app.executors.claude_stream_projection import ClaudeStreamProjector
from app.executors.public_answer_stream import PublicAnswerStreamGate
from app.required_tool_contract import (
    REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY,
    SANDBOX_EFFECTFUL_TOOL_IDENTITIES,
    SANDBOX_LOCAL_TOOL_IDENTITIES,
    SANDBOX_READ_ONLY_TOOL_IDENTITIES,
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    canonical_tool_call_id,
    declaration_from_input,
    declaration_from_payload,
    with_sandbox_local_tool_capability_subjects,
)
from app.settings import get_settings
from app.skills.execution_profiles import (
    NATIVE_COMMAND_ISOLATION,
    SKILL_WORKSPACE_CONTRACT_VERSION,
)
from app.tool_policy import evaluate_tool_policy

_context_pack_prompt_section = _prompt_context_pack_prompt_section
_translation_target_language = _prompt_translation_target_language


def runtime_tool_policy_subjects(
    payload: Any,
    context_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    value = payload.input.get("_runtime_tool_policy_subjects")
    subjects = (
        [
            dict(item)
            for item in value
            if isinstance(item, dict)
            and not str(item.get("identity") or "").startswith(
                "mcp__ai-platform-context__"
            )
        ]
        if isinstance(value, list)
        else []
    )
    subjects.extend(
        internal_context_tool_policy_subjects(
            available_context_retrieval_tools(context_manifest)
        )
    )
    return subjects


def sandbox_runtime_tool_policy_subjects(
    payload: Any,
    context_manifest: dict[str, Any] | None = None,
    *,
    sandbox_provider: str,
) -> list[dict[str, Any]]:
    return with_sandbox_local_tool_capability_subjects(
        runtime_tool_policy_subjects(payload, context_manifest),
        sandbox_provider=sandbox_provider,
        required_declaration=declaration_from_input(payload.input),
    )

_SDK_ENV_ALLOWLIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "COMSPEC",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "LANG",
    "LC_ALL",
}

_SDK_BASE_AVAILABLE_TOOLS = ["Read", "Glob", "LS"]
# Claude Agent SDK invokes custom subagents through the built-in Agent tool.
_SDK_SUBAGENT_TOOLS = ["Agent"]
_SDK_AVAILABLE_TOOLS = [*_SDK_BASE_AVAILABLE_TOOLS, *_SDK_SUBAGENT_TOOLS]
_SDK_AUTO_ALLOWED_TOOLS = {"Read", "Glob", "LS"}
_SDK_PLATFORM_DISALLOWED_TOOLS = ["Write", "Edit", "NotebookEdit"]
_SDK_LOCAL_READ_ONLY_TOOLS = ("Read", "Glob", "LS")
_SDK_BROKERED_BUILTIN_TOOLS = (
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
    "Agent",
    "WebFetch",
    "WebSearch",
)
_SDK_SELECTED_SKILL_NOT_INVOKED = "claude_agent_sdk_selected_skill_not_invoked"
_SDK_SELECTED_SKILL_HOOK_FAILED = "claude_agent_sdk_selected_skill_hook_failed"
_SDK_SELECTED_SKILL_NOT_AUTHORIZED = "claude_agent_sdk_selected_skill_not_authorized"
_SDK_TURN_LIMIT_EXCEEDED = "claude_agent_sdk_turn_limit_exceeded"
_SDK_CANCELLED = "claude_agent_sdk_cancelled"
_SDK_TIMEOUT = "claude_agent_sdk_timeout"
_SDK_MISSING_STRUCTURED_TERMINAL = "claude_agent_sdk_missing_structured_terminal"
_MAX_REQUIRED_ANSWER_TEXT_CHARS = 262_144
_MAX_PUBLIC_DELTA_CHARS = 8_192
_SDK_TOOL_ADMISSION_FAILED = "claude_agent_sdk_tool_admission_failed"
_SDK_UPSTREAM_ERROR = "claude_agent_sdk_upstream_error"
SDK_TURN_DIAGNOSTICS_SCHEMA_VERSION = "ai-platform.sdk-turn-diagnostics.v1"
_MAX_TURN_DIAGNOSTIC_COUNTER = 1_000_000
_MAX_PUBLIC_DIAGNOSTIC_SKILLS = 16
_MAX_PUBLIC_TOOL_POLICY_DENIALS = 8
_TOOL_POLICY_DENIAL_MAX_NAME_BYTES = 256
_TOOL_POLICY_DENIAL_MAX_REASON_BYTES = 512
_PUBLIC_DIAGNOSTIC_STAGES = frozenset({"planning", "runtime", "message", "skills"})
_PUBLIC_DIAGNOSTIC_COUNTERS = (
    "max_turns",
    "turns_observed",
    "assistant_messages",
    "text_blocks",
    "result_messages",
    "tool_admission_denials",
    "tool_policy_denials",
    "tool_lifecycle_denials",
    "skill_invocations",
)
_TURN_LIMIT_ERROR_PATTERN = re.compile(
    r"(?:reached\s+)?maximum\s+(?:number\s+of\s+)?turns|"
    r"max(?:imum)?[_ -]?turns?(?:[_ -]?(?:exceeded|reached))?",
    re.IGNORECASE,
)
_SDK_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SDK_PROJECT_SETTING_FILES = (".claude/settings.json", ".claude/settings.local.json")
_SDK_FULL_ACCESS_MIN_TIMEOUT_SECONDS = 1800.0


def _sdk_run_timeout_seconds(
    settings: object,
    *,
    sandbox_brokered: bool,
    full_access: bool,
) -> float | None:
    """Return the bounded SDK execution time, or None for unbounded runs.

    A configured value <= 0 disables the internal SDK execution deadline
    entirely (internal beta: tasks run until they finish). Explicit positive
    values still bound the run, so operators can re-enable a cap later.
    """

    timeout_seconds = float(getattr(settings, "claude_agent_sdk_timeout_seconds", 0.0))
    if timeout_seconds <= 0:
        return None
    return timeout_seconds


@dataclass(frozen=True)
class ClaudeAgentSdkRunResult:
    used_sdk: bool
    message: str = ""
    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # ResultMessage.stop_reason is meaningful only on a structured, non-error
    # SDK terminal result.  Keep it separate from the failure text.
    terminal_reason: str | None = None
    # A successful stream is valid only after the SDK supplies this structured
    # non-error ResultMessage.  Assistant chunks are progress, not completion.
    received_structured_terminal: bool = False
    used_skills: list[str] = field(default_factory=list)
    used_skills_source: str = ""
    turn_diagnostics: dict[str, Any] = field(default_factory=dict)
    capability_evidence: list[dict[str, str]] = field(default_factory=list)


class ClaudeAgentSdkNotAvailable(RuntimeError):
    pass


def _bounded_diagnostic_counter(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, min(value, _MAX_TURN_DIAGNOSTIC_COUNTER))


def _diagnostic_terminal_class(error_code: str | None) -> tuple[str, str | None, str, bool]:
    if not error_code:
        return "completed", None, "none", False
    if error_code in {"executor_cancelled", "claude_agent_sdk_cancelled"}:
        return "cancelled", "executor_cancelled", "none", False
    if error_code == _SDK_TURN_LIMIT_EXCEEDED:
        return "max_turn_exhausted", _SDK_TURN_LIMIT_EXCEEDED, "continue_or_narrow_request", True
    if error_code == _SDK_TIMEOUT:
        return "timeout", _SDK_TIMEOUT, "retry_or_split_request", True
    if error_code in {_SDK_MISSING_STRUCTURED_TERMINAL, "executor_missing_structured_terminal"}:
        return "missing_terminal", _SDK_MISSING_STRUCTURED_TERMINAL, "retry_request", True
    if error_code == _SDK_SELECTED_SKILL_NOT_INVOKED:
        return (
            "selected_skill_not_invoked",
            _SDK_SELECTED_SKILL_NOT_INVOKED,
            "retry_selected_skill",
            True,
        )
    if error_code in {
        _SDK_SELECTED_SKILL_HOOK_FAILED,
        _SDK_SELECTED_SKILL_NOT_AUTHORIZED,
        _SDK_TOOL_ADMISSION_FAILED,
        "claude_agent_sdk_disabled",
        "attachment_context_invalid",
        "context_retrieval_registration_failed",
        "context_retrieval_registration_unavailable",
    } or error_code.startswith("project_settings_scrub_failed"):
        return (
            "tool_policy_or_admission_failure",
            _SDK_TOOL_ADMISSION_FAILED,
            "review_skill_or_tool_admission",
            False,
        )
    return "upstream_error", _SDK_UPSTREAM_ERROR, "retry_later", True


def _public_tool_policy_denials(raw: object) -> list[dict[str, str]]:
    """Return a strict public-safe projection of tool admission denials.

    Each denial carries only the requested tool name and the policy reason
    code (no tool inputs, file content, or other private data).
    """

    if not isinstance(raw, list):
        return []
    projected: list[dict[str, str]] = []
    for item in raw:
        if len(projected) >= _MAX_PUBLIC_TOOL_POLICY_DENIALS:
            break
        if not isinstance(item, dict):
            continue
        tool_name = str(sanitize_public_payload(item.get("tool_name") or "")).strip()
        reason = str(sanitize_public_payload(item.get("reason") or "")).strip()
        if not tool_name or not reason:
            continue
        projected.append(
            {
                "tool_name": truncate_utf8_text(tool_name, max_bytes=_TOOL_POLICY_DENIAL_MAX_NAME_BYTES),
                "reason": truncate_utf8_text(reason, max_bytes=_TOOL_POLICY_DENIAL_MAX_REASON_BYTES),
            }
        )
    return projected


def _public_diagnostic_skill(
    skill_id: str | None,
    public_skill_metadata: dict[str, dict[str, str]] | None,
) -> dict[str, str] | None:
    metadata = (public_skill_metadata or {}).get(skill_id)
    if not isinstance(metadata, dict):
        return None
    name = truncate_utf8_text(
        str(sanitize_public_payload(metadata.get("name") or "")).strip(),
        max_bytes=256,
    )
    version = truncate_utf8_text(
        str(sanitize_public_payload(metadata.get("version") or "")).strip(),
        max_bytes=128,
    )
    availability = str(metadata.get("availability") or "").strip()
    if not name or not version or availability not in {
        "available",
        "unavailable_dependency",
        "unavailable_materialization",
    }:
        return None
    return {"name": name, "version": version, "availability": availability}


def project_sdk_turn_diagnostics(
    value: object,
    *,
    error_code: str | None,
    selected_skill_id: str = "",
    used_skill_ids: list[str] | None = None,
    public_skill_metadata: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return the strict public-safe projection for one SDK terminal outcome."""

    raw = value if isinstance(value, dict) else {}
    raw_counters = raw.get("counters") if isinstance(raw.get("counters"), dict) else {}
    counters = {
        name: _bounded_diagnostic_counter(raw_counters.get(name))
        for name in _PUBLIC_DIAGNOSTIC_COUNTERS
    }
    last_public_stage = str(raw.get("last_public_stage") or "runtime")
    if last_public_stage not in _PUBLIC_DIAGNOSTIC_STAGES:
        last_public_stage = "runtime"
    terminal_class, public_error_code, action, retryable = _diagnostic_terminal_class(
        str(error_code or "") or None
    )
    selected_skill = _public_diagnostic_skill(selected_skill_id, public_skill_metadata)
    used_skills: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for skill_id in used_skill_ids or []:
        if len(used_skills) >= _MAX_PUBLIC_DIAGNOSTIC_SKILLS:
            break
        if skill_id in seen_ids:
            continue
        seen_ids.add(skill_id)
        metadata = _public_diagnostic_skill(skill_id, public_skill_metadata)
        if metadata is not None:
            used_skills.append(metadata)
    tool_policy_denials_detail = _public_tool_policy_denials(
        raw_counters.get("tool_policy_denials_detail")
    )
    return {
        "schema_version": SDK_TURN_DIAGNOSTICS_SCHEMA_VERSION,
        "terminal_class": terminal_class,
        "error_code": public_error_code,
        "action": action,
        "retryable": retryable,
        "counters": counters,
        "last_public_stage": last_public_stage,
        "selected_skill": selected_skill,
        "used_skills": used_skills,
        "tool_policy_denials_detail": tool_policy_denials_detail,
    }


def _canonical_sdk_error(
    raw_error: object,
    *,
    result_subtype: object = "",
    stop_reason: object = "",
    terminal_reason: object = "",
    selected_skill_error: str | None = None,
    tool_admission_denials: int = 0,
) -> str:
    error_text = str(raw_error or "").strip()
    subtype = str(result_subtype or "").strip().casefold()
    stop = str(stop_reason or "").strip().casefold()
    terminal = str(terminal_reason or "").strip().casefold()
    if (
        subtype in {"error_max_turns", "max_turns", "max_turns_exceeded"}
        or stop in {"max_turns", "max_turns_exceeded"}
        or terminal in {"max_turns", "max_turns_exceeded"}
        or _TURN_LIMIT_ERROR_PATTERN.search(error_text)
    ):
        return _SDK_TURN_LIMIT_EXCEEDED
    if terminal in {"aborted_streaming", "aborted_tools", "cancelled", "canceled"}:
        return _SDK_CANCELLED
    if error_text == _SDK_TIMEOUT:
        return _SDK_TIMEOUT
    if error_text == _SDK_MISSING_STRUCTURED_TERMINAL:
        return _SDK_MISSING_STRUCTURED_TERMINAL
    if selected_skill_error:
        return selected_skill_error
    if tool_admission_denials > 0:
        return _SDK_TOOL_ADMISSION_FAILED
    if error_text in {
        _SDK_SELECTED_SKILL_HOOK_FAILED,
        _SDK_SELECTED_SKILL_NOT_AUTHORIZED,
        "attachment_context_invalid",
        "context_retrieval_registration_failed",
        "context_retrieval_registration_unavailable",
    } or error_text.startswith("project_settings_scrub_failed"):
        return error_text
    return _SDK_UPSTREAM_ERROR


ScopedContextRetrievalIdentity = ContextRetrievalIdentity


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _scrub_project_setting_files(cwd: Path) -> None:
    for relative_path in _SDK_PROJECT_SETTING_FILES:
        path = cwd / relative_path
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_file() or path.is_symlink():
            path.unlink()
            continue
        raise OSError(f"unsupported project settings path: {relative_path}")


def _safe_permission_mode(value: object) -> str:
    mode = str(value or "dontAsk").strip() or "dontAsk"
    if mode in {"default", "plan", "acceptEdits", "bypassPermissions", "dontAsk"}:
        return mode
    return "dontAsk"


def _full_access_requested(settings: object) -> bool:
    return _safe_permission_mode(getattr(settings, "claude_agent_permission_mode", "dontAsk")) == "bypassPermissions"


def _sdk_permission_mode(value: object, *, full_access: bool = False) -> str:
    mode = _safe_permission_mode(value)
    if full_access and mode == "bypassPermissions":
        # Claude CLI refuses its dangerous skip-permissions flag under root.
        # Platform full access is enforced below through tools, hooks, and can_use_tool.
        return "dontAsk"
    return mode


def _safe_allowed_tools(value: object, *, full_access: bool = False) -> list[str]:
    if full_access:
        return _sdk_tools_for_mode(full_access=True)
    allowed: list[str] = []
    for tool_name in _split_csv(str(value or "Read,Glob,LS")):
        if tool_name in _SDK_AUTO_ALLOWED_TOOLS and tool_name not in allowed:
            allowed.append(tool_name)
    return allowed


def _safe_disallowed_tools(value: object, *, full_access: bool = False) -> list[str]:
    if full_access:
        return []
    disallowed: list[str] = []
    for tool_name in _SDK_PLATFORM_DISALLOWED_TOOLS + _split_csv(str(value or "")):
        if tool_name == "Bash" or tool_name in disallowed:
            continue
        disallowed.append(tool_name)
    return disallowed


def _sdk_tools_for_mode(*, full_access: bool = False, include_skill: bool = False) -> list[str]:
    tools = list(_SDK_AVAILABLE_TOOLS if full_access else _SDK_BASE_AVAILABLE_TOOLS)
    if include_skill and "Skill" not in tools:
        tools.append("Skill")
    return tools


def _sdk_skill_allow_patterns(skill_names: set[str]) -> list[str]:
    """Return exact Claude SDK permission patterns for staged, authorized Skills."""

    return [f"Skill({name})" for name in sorted(skill_names)]


def _sdk_permission_type(sdk: object, name: str):
    permission_type = getattr(sdk, name, None)
    if permission_type is None:
        permission_type = getattr(getattr(sdk, "types", None), name, None)
    if permission_type is not None:
        return permission_type

    default_behavior = "allow" if name.endswith("Allow") else "deny"

    class PermissionResult:
        def __init__(self, behavior: str = default_behavior, message: str = "", interrupt: bool = False):
            self.behavior = behavior
            self.message = message
            self.interrupt = interrupt

    return PermissionResult


def build_sdk_env(*, cwd: Path | None = None) -> dict[str, str]:
    settings = get_settings()
    env = {key: "" for key in os.environ if key not in _SDK_ENV_ALLOWLIST}
    for key in _SDK_ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            env[key] = value
    if cwd is not None:
        home = cwd / ".home"
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["CLAUDE_CONFIG_DIR"] = str(cwd / ".claude-config")
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
        env["TMPDIR"] = str(cwd / ".tmp")
        env["TMP"] = str(cwd / ".tmp")
        env["TEMP"] = str(cwd / ".tmp")
    if settings.anthropic_base_url:
        env["ANTHROPIC_BASE_URL"] = settings.anthropic_base_url
    if settings.anthropic_auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = settings.anthropic_auth_token
    if settings.anthropic_model:
        env["ANTHROPIC_MODEL"] = settings.anthropic_model
    if settings.openai_api_key and not env.get("ANTHROPIC_AUTH_TOKEN"):
        env["ANTHROPIC_AUTH_TOKEN"] = settings.openai_api_key
    for key in ("AI_PLATFORM_NATIVE_TOOL_SOCKET", "AI_PLATFORM_NATIVE_TOOL_TOKEN"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


async def _sdk_user_prompt_stream(
    prompt: str,
    *,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": session_id or "default",
    }


def _context_retrieval_tool_response(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_public_payload(payload)
    if isinstance(sanitized, dict):
        workspace_path = _safe_retrieval_workspace_path(payload.get("workspace_path"))
        if workspace_path:
            sanitized["workspace_path"] = workspace_path
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(sanitized if isinstance(sanitized, dict) else {}, ensure_ascii=False),
            }
        ]
    }


def _safe_retrieval_workspace_path(value: object) -> str | None:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in ("storage_key", "raw_storage_key", "tenants/", "s3://", "private")):
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or path.parts[0] != "context":
        return None
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _context_retrieval_tool_error(reason: str, *, action: str = "context_retrieval.tool") -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": reason,
                        "audit": {
                            "action": action,
                            "result": "denied",
                            "reason": reason,
                        },
                        "redaction": {"object_locator_refs_removed": True},
                    },
                    ensure_ascii=False,
                ),
            }
        ],
        "is_error": True,
    }


def _build_context_retrieval_mcp_server(
    sdk: object,
    *,
    retrieval: ContextRetrievalAuthority | None,
    identity: ScopedContextRetrievalIdentity | None,
    tool_names: list[str] | None = None,
):
    if retrieval is None or identity is None:
        return None
    sdk_tool = getattr(sdk, "tool", None)
    create_server = getattr(sdk, "create_sdk_mcp_server", None)
    if sdk_tool is None or create_server is None:
        return None
    selected_tool_names = {
        name for name in (tool_names or _SDK_INTERNAL_CONTEXT_TOOLS) if name in _SDK_INTERNAL_CONTEXT_TOOLS
    }
    if not selected_tool_names:
        return None

    async def _run(action: str, args: object) -> dict[str, Any]:
        audit_action = f"context_retrieval.{action}"
        tool_args = args if isinstance(args, dict) else {}
        try:
            return _context_retrieval_tool_response(
                await retrieval.execute(action, identity, tool_args)
            )
        except ContextRetrievalInputError as exc:
            return _context_retrieval_tool_error(str(exc), action=audit_action)
        except ContextRetrievalDenied as exc:
            reason = str(exc) or "context_scope_denied"
            if reason not in {"context_file_too_large", "context_file_size_required"}:
                reason = "context_scope_denied"
            return _context_retrieval_tool_error(reason, action=audit_action)
        except Exception:  # noqa: BLE001
            return _context_retrieval_tool_error("context_retrieval_failed", action=audit_action)

    @sdk_tool(
        "read_session_messages",
        "Read prior messages for the current ai-platform run scope only.",
        {
            "limit": int,
            "offset": int,
            "max_tokens": int,
        },
    )
    async def read_session_messages(args):
        return await _run("read_session_messages", args)

    @sdk_tool(
        "read_run_artifact",
        "Read an artifact explicitly authorized by the current ai-platform run snapshot.",
        {
            "artifact_id": str,
            "max_bytes": int,
        },
    )
    async def read_run_artifact(args):
        return await _run("read_run_artifact", args)

    @sdk_tool(
        "stage_context_file_to_workspace",
        "Stage an uploaded context file into the current run workspace and return a workspace-relative path.",
        {
            "file_id": str,
            "max_bytes": int,
        },
    )
    async def stage_context_file_to_workspace(args):
        return await _run("stage_context_file_to_workspace", args)

    @sdk_tool(
        "stage_run_artifact_to_workspace",
        "Stage a current-snapshot-authorized run artifact into the workspace and return a workspace-relative path.",
        {
            "artifact_id": str,
            "max_bytes": int,
        },
    )
    async def stage_run_artifact_to_workspace(args):
        return await _run("stage_run_artifact_to_workspace", args)

    @sdk_tool(
        "search_memory",
        "Search active session-scoped memory records for the current ai-platform agent scope only.",
        {
            "query": str,
            "limit": int,
            "max_tokens": int,
        },
    )
    async def search_memory(args):
        return await _run("search_memory", args)

    return create_server(
        "ai-platform-context",
        version="1.0.0",
        tools=[
            tool
            for tool in (
                read_session_messages,
                read_run_artifact,
                stage_context_file_to_workspace,
                stage_run_artifact_to_workspace,
                search_memory,
            )
            if tool.name in selected_tool_names
        ],
    )


_WORKSPACE_PATH_PARAMETER = {
    "Read": "file_path",
    "LS": "path",
}
_WORKSPACE_MUTATING_PATH_PARAMETER = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}
_WORKSPACE_INTERNAL_ROOTS = frozenset(
    {".ai-platform", ".claude-config", ".home", ".pins", ".tmp"}
)
_NATIVE_TOOL_MAX_COMMAND_BYTES = 64 * 1024
_NATIVE_TOOL_DEFAULT_TIMEOUT_MS = 120_000
_NATIVE_TOOL_MAX_TIMEOUT_MS = 600_000
_NATIVE_TOOL_PROXY_SCRIPT = (
    Path(__file__).resolve().parents[1] / "runtime" / "sandbox" / "native_tool_proxy.py"
)


def _workspace_path_parameters_authorized(
    subject: dict[str, Any],
    tool_name: str,
    tool_input: object,
    *,
    workspace_root: Path,
) -> bool:
    mutating_key = _WORKSPACE_MUTATING_PATH_PARAMETER.get(tool_name)
    if (
        str(subject.get("workspace_contract") or "") != SKILL_WORKSPACE_CONTRACT_VERSION
        and mutating_key is None
    ):
        return True

    def normalized_relatives(raw: object) -> tuple[Path, Path] | None:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return None
        try:
            root = workspace_root.resolve(strict=True)
            candidate = Path(raw.replace("\\", os.sep).replace("/", os.sep))
            if not candidate.is_absolute():
                candidate = root / candidate
            lexical_relative = Path(os.path.abspath(candidate)).relative_to(root)
            resolved_relative = candidate.resolve(strict=False).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
        return lexical_relative, resolved_relative

    def readable_path_parts_authorized(relative: Path) -> bool:
        if not relative.parts:
            return True
        lowered = tuple(part.lower() for part in relative.parts)
        if lowered[0] in _WORKSPACE_INTERNAL_ROOTS:
            return False
        if lowered[0] == ".claude":
            return len(lowered) >= 2 and lowered[1] == "skills"
        return True

    def writable_path_parts_authorized(relative: Path) -> bool:
        lowered = tuple(part.lower() for part in relative.parts)
        if len(lowered) >= 2 and lowered[0] == "output":
            return True
        return (
            len(lowered) >= 3
            and lowered[0] == "outputs"
            and "delivery" in lowered[1:-1]
        )

    def path_authorized(raw: object, *, mutating: bool = False) -> bool:
        relatives = normalized_relatives(raw)
        if relatives is None:
            return False
        authorize = writable_path_parts_authorized if mutating else readable_path_parts_authorized
        return all(authorize(relative) for relative in relatives)

    def glob_pattern_authorized(raw: object, *, search_path: object) -> bool:
        if not path_authorized(raw):
            return False
        assert isinstance(raw, str)
        if ".." in raw or any(char in raw for char in "{}()!\\"):
            return False
        if not isinstance(search_path, str) or not search_path:
            return False
        try:
            root = workspace_root.resolve(strict=True)
            candidate = Path(search_path)
            if not candidate.is_absolute():
                candidate = root / candidate
            search_relative = candidate.resolve(strict=False).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return False
        if search_relative.parts:
            return True
        parts = tuple(
            part
            for part in raw.replace("\\", "/").split("/")
            if part not in {"", "."}
        )
        if not parts:
            return False
        first = parts[0]
        lowered = tuple(part.lower() for part in parts)
        if first.startswith("."):
            return len(lowered) >= 2 and lowered[:2] == (".claude", "skills")
        if len(parts) > 1 and not all(
            char.isalnum() or char in {"_", "-", "."}
            for char in first
        ):
            return False
        return first != "**"

    if not isinstance(tool_input, dict):
        return False
    if tool_name == "Glob":
        search_path = tool_input.get("path") or "."
        return path_authorized(search_path) and glob_pattern_authorized(
            tool_input.get("pattern"),
            search_path=search_path,
        )
    if tool_name == "Grep":
        search_path = tool_input.get("path") or "."
        glob = tool_input.get("glob")
        return path_authorized(search_path) and (
            glob is None or glob_pattern_authorized(glob, search_path=search_path)
        )
    if mutating_key is not None:
        return path_authorized(tool_input.get(mutating_key), mutating=True)
    key = _WORKSPACE_PATH_PARAMETER.get(tool_name)
    if key is None:
        return True
    return path_authorized(tool_input.get(key))


def _native_tool_proxy_input(tool_input: object) -> dict[str, Any] | None:
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    if len(command.encode("utf-8")) > _NATIVE_TOOL_MAX_COMMAND_BYTES:
        return None
    raw_timeout = tool_input.get("timeout")
    if raw_timeout is None:
        timeout_ms = _NATIVE_TOOL_DEFAULT_TIMEOUT_MS
    elif (
        isinstance(raw_timeout, int)
        and not isinstance(raw_timeout, bool)
        and 1 <= raw_timeout <= _NATIVE_TOOL_MAX_TIMEOUT_MS
    ):
        timeout_ms = raw_timeout
    else:
        return None
    socket_path = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_SOCKET") or "")
    token = str(os.getenv("AI_PLATFORM_NATIVE_TOOL_TOKEN") or "")
    if not socket_path or not token:
        return None
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    proxy_command = " ".join(
        (
            shlex.quote(sys.executable),
            "-I",
            shlex.quote(str(_NATIVE_TOOL_PROXY_SCRIPT)),
            shlex.quote(encoded),
            str(timeout_ms),
        )
    )
    return {"command": proxy_command, "timeout": timeout_ms}


async def run_claude_agent_sdk(
    *,
    prompt: str,
    cwd: Path,
    skill_id: str | None,
    session_id: str | None = None,
    context_retrieval: ContextRetrievalAuthority | None = None,
    context_retrieval_identity: ScopedContextRetrievalIdentity | None = None,
    model_id: str | None = None,
    system_prompt: str | None = None,
    skills: list[str] | None = None,
    query_fn: Callable[..., Any] | None = None,
    on_text: Callable[[str], Awaitable[None]] | None = None,
    on_skill_use: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    on_capability_evidence: Callable[[dict[str, str]], Awaitable[bool]] | None = None,
    on_tool_lifecycle: Callable[[dict[str, str]], Awaitable[bool]] | None = None,
    on_agent_event: Callable[[tuple[Any, ...]], Awaitable[bool | None] | bool | None] | None = None,
    run_id: str | None = None,
    attempt_id: str | None = None,
    tool_policy_subjects: list[dict[str, Any]] | None = None,
    execution_policy: str = "worker_local_legacy",
    public_skill_metadata: dict[str, dict[str, str]] | None = None,
    require_selected_skill_invocation: bool = True,
) -> ClaudeAgentSdkRunResult:
    settings = get_settings()
    max_turns = max(1, int(getattr(settings, "claude_agent_sdk_max_turns", 128)))
    diagnostic_counters = {
        "max_turns": max_turns,
        "turns_observed": 0,
        "assistant_messages": 0,
        "text_blocks": 0,
        "result_messages": 0,
        "tool_admission_denials": 0,
        "tool_policy_denials": 0,
        "tool_lifecycle_denials": 0,
        "skill_invocations": 0,
    }
    last_public_stage = "runtime"
    used_skill_names: list[str] = []
    capability_evidence: list[dict[str, str]] = []
    capability_evidence_rejected = False
    actual_mcp_invocation_observed = False
    observed_read_only_invocation_states: dict[tuple[str, str], str] = {}
    read_only_lifecycle_denials_finalized = False

    def finalize_read_only_lifecycle_denials() -> None:
        nonlocal read_only_lifecycle_denials_finalized
        if read_only_lifecycle_denials_finalized:
            return
        diagnostic_counters["tool_lifecycle_denials"] += sum(
            state == "started"
            for state in observed_read_only_invocation_states.values()
        )
        read_only_lifecycle_denials_finalized = True

    def turn_diagnostics(error_code: str | None) -> dict[str, Any]:
        finalize_read_only_lifecycle_denials()
        return project_sdk_turn_diagnostics(
            {
                "counters": diagnostic_counters,
                "last_public_stage": last_public_stage,
            },
            error_code=error_code,
            selected_skill_id=(
                skill_id
                if skill_id != LEGACY_SYNTHETIC_CHAT_SKILL_ID
                else ""
            )
            or "",
            used_skill_ids=list(used_skill_names),
            public_skill_metadata=public_skill_metadata,
        )

    if not settings.claude_agent_sdk_enabled:
        error_code = "claude_agent_sdk_disabled"
        return ClaudeAgentSdkRunResult(
            used_sdk=False,
            error=error_code,
            turn_diagnostics=turn_diagnostics(error_code),
        )
    try:
        import claude_agent_sdk as sdk

        AssistantMessage = sdk.AssistantMessage
        ClaudeAgentOptions = sdk.ClaudeAgentOptions
        ResultMessage = sdk.ResultMessage
        StreamEvent = getattr(sdk, "StreamEvent", ())
        TaskStartedMessage = getattr(sdk, "TaskStartedMessage", ())
        TaskProgressMessage = getattr(sdk, "TaskProgressMessage", ())
        TaskNotificationMessage = getattr(sdk, "TaskNotificationMessage", ())
        TaskUpdatedMessage = getattr(sdk, "TaskUpdatedMessage", ())
        ToolPermissionContext = getattr(sdk, "ToolPermissionContext", ())
        TextBlock = sdk.TextBlock
        HookMatcher = getattr(sdk, "HookMatcher", None)
        if query_fn is None:
            query = sdk.query
        else:
            query = query_fn
    except Exception as exc:
        raise ClaudeAgentSdkNotAvailable(str(exc)) from exc

    PermissionResultAllow = _sdk_permission_type(sdk, "PermissionResultAllow")
    PermissionResultDeny = _sdk_permission_type(sdk, "PermissionResultDeny")
    configured_skills = (
        skills
        if skills is not None
        else (_split_csv(settings.claude_agent_sdk_skills) or ([skill_id] if skill_id else []))
    )
    if any(
        not isinstance(name, str) or _SDK_SKILL_NAME_PATTERN.fullmatch(name) is None
        for name in configured_skills
    ):
        error_code = _SDK_TOOL_ADMISSION_FAILED
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            error=error_code,
            turn_diagnostics=turn_diagnostics(error_code),
        )
    selected_sdk_skill = (
        skill_id
        if skill_id not in {None, LEGACY_SYNTHETIC_CHAT_SKILL_ID}
        and skill_id in configured_skills
        else None
    )
    sandbox_partial_streaming = (
        on_text is not None
        and execution_policy == "sandbox_brokered"
    )
    failed_skill_names: list[str] = []
    sandbox_brokered = execution_policy == "sandbox_brokered"
    authorized_subjects = _canonical_tool_policy_subjects(tool_policy_subjects)
    if (
        sandbox_brokered
        and set(SANDBOX_LOCAL_TOOL_IDENTITIES).intersection(authorized_subjects)
        and HookMatcher is None
    ):
        error_code = _SDK_TOOL_ADMISSION_FAILED
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            error=error_code,
            turn_diagnostics=turn_diagnostics(error_code),
        )
    requested_internal_context_tools = [
        identity.removeprefix(_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX)
        for identity in authorized_subjects
        if identity.startswith(_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX)
        and identity.removeprefix(_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX) in _SDK_INTERNAL_CONTEXT_TOOLS
    ]
    if sandbox_brokered:
        for identity in list(authorized_subjects):
            if not identity.startswith(_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX):
                continue
            tool_name = identity.removeprefix(_SDK_INTERNAL_CONTEXT_IDENTITY_PREFIX)
            authorized_subjects.pop(identity, None)
    full_access = _full_access_requested(settings) and not sandbox_brokered
    permission_mode = (
        "dontAsk"
        if sandbox_brokered
        else _sdk_permission_mode(
            getattr(settings, "claude_agent_permission_mode", "dontAsk"),
            full_access=full_access,
        )
    )
    if sandbox_brokered:
        skill_subject = authorized_subjects.get("Skill")
        subject_skill_names = skill_subject.get("allowed_skill_names") if skill_subject else []
        allowed_skill_names = {
            name
            for name in subject_skill_names
            if isinstance(name, str) and name in set(configured_skills)
        }
        configured_skills = [name for name in configured_skills if name in allowed_skill_names]
        allowed_tools = [
            pattern
            for identity in authorized_subjects
            for pattern in (
                _sdk_skill_allow_patterns(allowed_skill_names)
                if identity == "Skill"
                else [identity]
            )
        ]
    else:
        allowed_skill_names = set(configured_skills)
        allowed_tools = [
            *_safe_allowed_tools(
                getattr(settings, "claude_agent_allowed_tools", "Read,Glob,LS"),
                full_access=full_access,
            ),
            *_sdk_skill_allow_patterns(allowed_skill_names),
        ]
    if selected_sdk_skill is not None and selected_sdk_skill not in allowed_skill_names:
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            error=_SDK_SELECTED_SKILL_NOT_AUTHORIZED,
            turn_diagnostics=turn_diagnostics(_SDK_SELECTED_SKILL_NOT_AUTHORIZED),
        )
    context_retrieval_registration_error: str | None = None
    try:
        context_retrieval_server = _build_context_retrieval_mcp_server(
            sdk,
            retrieval=context_retrieval,
            identity=context_retrieval_identity,
            tool_names=(
                requested_internal_context_tools
                if tool_policy_subjects is not None
                else list(_SDK_INTERNAL_CONTEXT_TOOLS)
            ),
        )
        if requested_internal_context_tools and context_retrieval_server is None:
            context_retrieval_registration_error = "context_retrieval_registration_unavailable"
    except Exception:  # noqa: BLE001
        context_retrieval_server = None
        context_retrieval_registration_error = "context_retrieval_registration_failed"
    if requested_internal_context_tools and context_retrieval_server is None:
        error_code = context_retrieval_registration_error or _SDK_TOOL_ADMISSION_FAILED
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            error=error_code,
            turn_diagnostics=turn_diagnostics(error_code),
        )
    if context_retrieval_server is None:
        internal_context_tools: set[str] = set()
    elif tool_policy_subjects is None:
        internal_context_tools = set(_SDK_INTERNAL_CONTEXT_TOOLS)
    else:
        internal_context_tools = set(requested_internal_context_tools)
    internal_context_subjects = (
        {
            str(subject["identity"]): subject
            for subject in internal_context_tool_policy_subjects(requested_internal_context_tools)
        }
        if context_retrieval_server is not None
        else {}
    )
    if sandbox_brokered:
        for identity in internal_context_subjects:
            if identity not in allowed_tools:
                allowed_tools.append(identity)
    if context_retrieval_server is not None and not sandbox_brokered:
        for tool_name in internal_context_tools:
            if tool_name not in allowed_tools:
                allowed_tools.append(tool_name)
    disallowed_tools = (
        []
        if sandbox_brokered
        else _safe_disallowed_tools(
            getattr(settings, "claude_agent_disallowed_tools", ""),
            full_access=full_access,
        )
    )
    try:
        mcp_servers = _mcp_server_options(authorized_subjects) if sandbox_brokered else {}
    except ValueError:
        return ClaudeAgentSdkRunResult(used_sdk=True, error=_SDK_TOOL_ADMISSION_FAILED, turn_diagnostics=turn_diagnostics(_SDK_TOOL_ADMISSION_FAILED))
    if context_retrieval_server is not None and (not sandbox_brokered or internal_context_subjects):
        mcp_servers["ai-platform-context"] = context_retrieval_server
    capability_plan = CapabilityExecutionPlan.from_tool_policy_subjects(
        tool_policy_subjects,
        required_skill_identity=(selected_sdk_skill if require_selected_skill_invocation else None),
        available_skill_identities=(
            allowed_skill_names if not require_selected_skill_invocation else ()
        ),
        registered_mcp_servers=mcp_servers,
    )
    required_capability_declarations = {
        (declaration.capability_kind, declaration.canonical_identity): declaration
        for declaration in capability_plan.required
    }
    required_builtin_declarations: dict[
        tuple[str, str], RequiredCapabilityDeclaration
    ] = {}
    try:
        for identity, subject in authorized_subjects.items():
            declaration = declaration_from_payload(
                subject.get(REQUIRED_CAPABILITY_DECLARATION_INPUT_KEY)
            )
            if declaration is None:
                continue
            if (
                declaration.capability_kind != "builtin"
                or declaration.canonical_identity != identity
            ):
                raise RequiredToolContractError("required_tool_declaration_mismatch")
            required_builtin_declarations[("builtin", identity)] = declaration
    except RequiredToolContractError:
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            error=_SDK_TOOL_ADMISSION_FAILED,
            turn_diagnostics=turn_diagnostics(_SDK_TOOL_ADMISSION_FAILED),
        )
    sandbox_local_lifecycle_names = {
        identity
        for identity in SANDBOX_LOCAL_TOOL_IDENTITIES
        if sandbox_brokered and identity in authorized_subjects
    }
    strict_tool_lifecycle_names = (
        sandbox_local_lifecycle_names & SANDBOX_EFFECTFUL_TOOL_IDENTITIES
    )
    read_only_tool_lifecycle_names = (
        sandbox_local_lifecycle_names & SANDBOX_READ_ONLY_TOOL_IDENTITIES
    )
    if sandbox_brokered and internal_context_subjects:
        strict_tool_lifecycle_names.add("MCP")
    sandbox_bash_lifecycle_governed = "Bash" in strict_tool_lifecycle_names
    sandbox_tool_lifecycle_governed = bool(strict_tool_lifecycle_names)
    effectful_mcp_lifecycle_governed = {
        identity
        for identity, subject in authorized_subjects.items()
        if identity.startswith("mcp__") and subject.get("write_capable") is True
    }
    required_answer_gate = bool(
        required_capability_declarations
        or required_builtin_declarations
        or sandbox_tool_lifecycle_governed
        or effectful_mcp_lifecycle_governed
    )
    governed_builtin_invocation_states: dict[tuple[str, str], str] = {}
    governed_builtin_lifecycle_rejected = False
    actual_sandbox_bash_invocation_observed = False
    private_mcp_replacements = {
        identity: "external tool"
        for kind, identity in capability_plan.available
        if kind == "mcp"
    }
    private_mcp_replacements.update(
        {
            str(config["url"]): "external tool endpoint"
            for server_id, config in mcp_servers.items()
            if server_id != "ai-platform-context"
            and isinstance(config, dict)
            and isinstance(config.get("url"), str)
            and config["url"]
        }
    )
    answer_stream_gate = PublicAnswerStreamGate(
        private_replacements=private_mcp_replacements,
        sanitizer=sanitize_public_text,
        max_sealed_chars=_MAX_REQUIRED_ANSWER_TEXT_CHARS,
    )
    # Do not publish answer prefixes before the structured terminal result has
    # validated the whole-answer bound; an overflow cannot retract prior SSE.
    answer_stream_gate.defer_until_finish()
    sdk_prompt = (
        _with_selected_skill_invocation_requirement(prompt, selected_sdk_skill)
        if require_selected_skill_invocation
        else prompt
    )
    timeout_seconds = _sdk_run_timeout_seconds(
        settings,
        sandbox_brokered=sandbox_brokered,
        full_access=full_access,
    )

    agent_event_adapter = (
        ClaudeSdkAgentEventAdapter(
            run_id=run_id,
            attempt_id=attempt_id,
            tool_policy_subjects=tool_policy_subjects,
            public_skill_metadata=public_skill_metadata,
            sanitizer=sanitize_public_text,
            payload_sanitizer=sanitize_public_payload,
        )
        if run_id and attempt_id and on_agent_event is not None
        else None
    )

    agent_event_callback_failed = False

    async def publish_agent_candidates(candidates: tuple[Any, ...]) -> bool:
        nonlocal agent_event_callback_failed
        if not candidates:
            return True
        if agent_event_adapter is None or on_agent_event is None or agent_event_callback_failed:
            return not agent_event_callback_failed
        try:
            callback_result = on_agent_event(candidates)
            if isawaitable(callback_result):
                callback_result = await callback_result
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling() > 0:
                raise
            callback_result = False
        except Exception:  # noqa: BLE001
            callback_result = False
        if callback_result is not True:
            agent_event_callback_failed = True
            seal_agent_candidates("agent_event_callback_not_acknowledged")
            return False
        return True

    def seal_agent_candidates(reason: str) -> None:
        if agent_event_adapter is not None:
            agent_event_adapter.seal(reason)

    def claim_used_skill(skill_name: str) -> bool:
        nonlocal last_public_stage
        if skill_name not in allowed_skill_names or skill_name in used_skill_names:
            return False
        used_skill_names.append(skill_name)
        diagnostic_counters["skill_invocations"] += 1
        last_public_stage = "skills"
        return True

    def reject_capability_evidence() -> bool:
        nonlocal capability_evidence_rejected
        if not capability_evidence_rejected:
            capability_evidence_rejected = True
            capability_evidence.clear()
            used_skill_names.clear()
        return False

    async def record_capability_evidence(*, capability_kind: str, canonical_identity: str, tool_call_id: str,
                                         lifecycle_phase: str, skill_metadata: dict[str, Any] | None = None) -> bool:
        """Record one bounded actual-call fact without tool input or output."""

        nonlocal actual_mcp_invocation_observed
        if capability_evidence_rejected:
            return False
        key = (capability_kind, canonical_identity)
        if capability_kind == "mcp":
            actual_mcp_invocation_observed = True
            answer_stream_gate.seal(
                {
                    canonical_identity: "external tool",
                    tool_call_id: "tool invocation",
                }
            )
        elif key in capability_plan.available:
            answer_stream_gate.seal({tool_call_id: "tool invocation"})
        try:
            evidence = RequiredCapabilityEvidence.sdk_hook_payload(
                declaration=RequiredCapabilityDeclaration.from_authorized_subject(
                    capability_kind=capability_kind, canonical_identity=canonical_identity
                ),
                tool_call_id=tool_call_id,
                lifecycle_phase=lifecycle_phase,
            ) if key in capability_plan.available else None
            if evidence is not None:
                acknowledged = await on_capability_evidence(dict(evidence)) if on_capability_evidence else False
            elif capability_kind == "mcp":
                return reject_capability_evidence()
        except asyncio.CancelledError:
            reject_capability_evidence()
            raise
        except Exception:  # noqa: BLE001
            return reject_capability_evidence()
        if evidence is not None:
            # One event-loop task executes this no-await commit section at a time.
            if acknowledged is not True:
                return reject_capability_evidence()
            if capability_evidence_rejected:
                return False
            capability_evidence.append(evidence)
            if (
                lifecycle_phase == "completed"
                and capability_completion_error() is None
            ):
                answer_stream_gate.release_after_verified_capability()
        claimed = skill_metadata is not None and claim_used_skill(canonical_identity)
        if claimed and on_skill_use:
            await on_skill_use(canonical_identity, skill_metadata)
        return not capability_evidence_rejected

    def exact_hook_tool_call_id(hook_input: dict[str, Any], tool_use_id: object) -> str:
        """Resolve the SDK's duplicated call-id fields only when they agree exactly."""

        supplied = tuple(
            value
            for value in (hook_input.get("tool_use_id"), tool_use_id)
            if value is not None and value != ""
        )
        canonical = tuple(canonical_tool_call_id(value) for value in supplied)
        if not supplied or any(value is None for value in canonical):
            return ""
        return canonical[0] if len(set(canonical)) == 1 else ""

    def reject_governed_lifecycle() -> bool:
        nonlocal governed_builtin_lifecycle_rejected

        governed_builtin_lifecycle_rejected = True
        diagnostic_counters["tool_lifecycle_denials"] += 1
        answer_stream_gate.fail_closed()
        return False

    def record_read_only_lifecycle_denial() -> None:
        diagnostic_counters["tool_lifecycle_denials"] += 1

    async def record_tool_lifecycle(
        *, tool_name: object, tool_call_id: object, lifecycle: str
    ) -> bool:
        """Report a private actual-tool fact and enforce strict evidence where needed."""

        nonlocal actual_sandbox_bash_invocation_observed

        name = str(tool_name or "").strip()
        call_id = canonical_tool_call_id(tool_call_id) or ""
        required_key = ("builtin", name)
        is_required_builtin = required_key in required_builtin_declarations
        is_read_only_tool = name in read_only_tool_lifecycle_names
        lifecycle_required = (
            is_required_builtin or name in strict_tool_lifecycle_names
        )
        lifecycle_observed = lifecycle_required or is_read_only_tool
        is_sandbox_bash = sandbox_bash_lifecycle_governed and name == "Bash"
        if is_sandbox_bash:
            actual_sandbox_bash_invocation_observed = True
        if lifecycle_required and call_id:
            answer_stream_gate.seal({call_id: "tool invocation"})
        if not name or not call_id or lifecycle not in {"started", "completed", "failed"}:
            if lifecycle_required:
                return reject_governed_lifecycle()
            if is_read_only_tool:
                record_read_only_lifecycle_denial()
            return True
        if on_tool_lifecycle is None:
            if lifecycle_required:
                return reject_governed_lifecycle()
            if is_read_only_tool:
                record_read_only_lifecycle_denial()
            return True
        try:
            acknowledged = await on_tool_lifecycle(
                {
                    "fact_kind": "tool_invocation",
                    "tool_name": name,
                    "invocation_id": call_id,
                    "lifecycle": lifecycle,
                }
            )
        except Exception:  # noqa: BLE001
            acknowledged = False
        if not lifecycle_observed:
            return True
        if acknowledged is not True:
            if lifecycle_required:
                governed_builtin_invocation_states[(name, call_id)] = "rejected"
                return reject_governed_lifecycle()
            record_read_only_lifecycle_denial()
            return True
        invocation_states = (
            governed_builtin_invocation_states
            if lifecycle_required
            else observed_read_only_invocation_states
        )
        invocation_key = (name, call_id)
        current_state = invocation_states.get(invocation_key)
        invalid_sequence = (
            lifecycle == "started" and current_state is not None
        ) or (
            lifecycle in {"completed", "failed"} and current_state != "started"
        )
        if invalid_sequence:
            invocation_states[invocation_key] = "rejected"
            if lifecycle_required:
                return reject_governed_lifecycle()
            record_read_only_lifecycle_denial()
            return True
        invocation_states[invocation_key] = lifecycle
        if lifecycle == "completed" and capability_completion_error() is None:
            answer_stream_gate.release_after_verified_capability()
        if lifecycle == "failed" and is_required_builtin:
            return reject_governed_lifecycle()
        return True

    def selected_skill_hook_error() -> str | None:
        if not require_selected_skill_invocation:
            return None
        if selected_sdk_skill is None or selected_sdk_skill in used_skill_names:
            return None
        if selected_sdk_skill in failed_skill_names:
            return _SDK_SELECTED_SKILL_HOOK_FAILED
        return _SDK_SELECTED_SKILL_NOT_INVOKED

    declared_tool_identities = (
        set(authorized_subjects) | set(internal_context_subjects)
        if sandbox_brokered
        else {
            (f"mcp__ai-platform-context__{tool_name}" if tool_name in internal_context_tools else tool_name)
            for tool_name in allowed_tools
        }
    )
    if not sandbox_brokered and allowed_skill_names:
        declared_tool_identities.add("Skill")

    def adapter_identity(tool_name: object) -> str:
        value = str(tool_name or "")
        contextual_identity = f"mcp__ai-platform-context__{value}"
        if contextual_identity in declared_tool_identities:
            return contextual_identity
        return value

    def policy_for_tool(tool_name: object, tool_input: object):
        identity = adapter_identity(tool_name)
        selected_skills = (
            _extract_skill_names_from_tool_input(tool_input, allowed_skill_names)
            if str(tool_name or "") == "Skill" and isinstance(tool_input, dict)
            else []
        )
        subject = internal_context_subjects.get(identity) or authorized_subjects.get(identity)
        if sandbox_brokered:
            subject_tool_name = (
                identity.rsplit("__", 1)[-1]
                if identity in internal_context_subjects
                else str(tool_name or "")
            )
            parameters_authorized = bool(subject) and _parameters_match_subject(
                subject,
                subject_tool_name,
                tool_input,
            )
            if parameters_authorized and subject is not None:
                parameters_authorized = _workspace_path_parameters_authorized(
                    subject,
                    subject_tool_name,
                    tool_input,
                    workspace_root=cwd,
                )
            if (
                parameters_authorized
                and subject_tool_name == "Bash"
                and str((subject or {}).get("command_isolation") or "") == NATIVE_COMMAND_ISOLATION
            ):
                parameters_authorized = _native_tool_proxy_input(tool_input) is not None
            registered = bool(subject) and (
                not identity.startswith("mcp__") or str(subject.get("mcp_server") or "") in mcp_servers
            )
            return evaluate_tool_policy(
                tool={
                    "requested_identity": identity,
                    "declared_identities": sorted(declared_tool_identities),
                    "registered": subject.get("registered") is True and registered if subject else False,
                    "declared": subject.get("declared") if subject else False,
                    "active": subject.get("active") if subject else False,
                    "distributed": subject.get("distributed") if subject else False,
                    "identity_authorized": subject.get("identity_authorized") if subject else False,
                    "object_authorized": subject.get("object_authorized") if subject else False,
                    "parameters_authorized": parameters_authorized,
                    "risk_level": subject.get("risk_level") if subject else "low",
                    "write_capable": subject.get("write_capable") if subject else False,
                }
            )
        parameters_authorized = isinstance(tool_input, dict)
        if str(tool_name or "") == "Bash":
            parameters_authorized = parameters_authorized and isinstance(tool_input.get("command"), str) and bool(tool_input["command"].strip())
        if str(tool_name or "") == "Skill":
            parameters_authorized = bool(selected_skills)
        declared = identity in declared_tool_identities
        return evaluate_tool_policy(
            tool={
                "requested_identity": identity,
                "declared_identities": sorted(declared_tool_identities),
                "registered": declared,
                "declared": declared,
                "active": declared,
                "distributed": declared,
                "identity_authorized": True,
                "object_authorized": True,
                "parameters_authorized": parameters_authorized,
                "risk_level": "low" if str(tool_name or "") in _SDK_LOCAL_READ_ONLY_TOOLS else "high",
                "write_capable": str(tool_name or "") not in _SDK_LOCAL_READ_ONLY_TOOLS,
            }
        )

    def permission_context_tool_use_id(context: object) -> object:
        if ToolPermissionContext and isinstance(context, ToolPermissionContext):
            return getattr(context, "tool_use_id", None)
        if isinstance(context, dict):
            return context.get("tool_use_id")
        return None

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], _context=None):
        decision = policy_for_tool(tool_name, tool_input)
        if not decision.allowed:
            diagnostic_counters["tool_admission_denials"] += 1
            diagnostic_counters["tool_policy_denials"] += 1
            diagnostic_counters.setdefault("tool_policy_denials_detail", []).append(
                {"tool_name": tool_name, "reason": decision.reason}
            )
            context_tool_use_id = permission_context_tool_use_id(_context)
            if agent_event_adapter is not None:
                await publish_agent_candidates(
                    agent_event_adapter.accept_policy_decision(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        allowed=False,
                        tool_use_id=context_tool_use_id,
                    )
                )
            return PermissionResultDeny(message=decision.reason)
        return PermissionResultAllow()

    async def enforce_side_effect_tool_policy(hook_input, tool_use_id=None, _context=None) -> dict[str, object]:
        hook_input_is_mapping = isinstance(hook_input, dict)
        hook_input = hook_input if hook_input_is_mapping else {}
        tool_name = ""
        if not hook_input_is_mapping:
            decision = evaluate_tool_policy(tool={})
        else:
            tool_name = str(hook_input.get("tool_name") or "")
            tool_input = hook_input.get("tool_input")
            decision = policy_for_tool(tool_name, tool_input)
        if not decision.allowed:
            diagnostic_counters["tool_admission_denials"] += 1
            diagnostic_counters["tool_policy_denials"] += 1
            diagnostic_counters.setdefault("tool_policy_denials_detail", []).append(
                {"tool_name": tool_name, "reason": decision.reason}
            )
        output: dict[str, object] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.outcome,
            "permissionDecisionReason": decision.reason,
        }
        if decision.allowed:
            tool_name = str(hook_input.get("tool_name") or "")
            identity = adapter_identity(tool_name)
            subject = internal_context_subjects.get(identity) or authorized_subjects.get(identity)
            if (
                tool_name == "Bash"
                and isinstance(subject, dict)
                and str(subject.get("command_isolation") or "") == NATIVE_COMMAND_ISOLATION
            ):
                updated_input = _native_tool_proxy_input(hook_input.get("tool_input"))
                if updated_input is None:
                    output["permissionDecision"] = "deny"
                    output["permissionDecisionReason"] = "native_tool_isolation_unavailable"
                else:
                    output["updatedInput"] = updated_input
        resolved_tool_call_id = exact_hook_tool_call_id(hook_input, tool_use_id)
        if agent_event_adapter is not None:
            await publish_agent_candidates(
                agent_event_adapter.accept_policy_decision(
                    tool_name=tool_name,
                    tool_input=hook_input.get("tool_input"),
                    allowed=decision.allowed is True and output["permissionDecision"] == "allow",
                    tool_use_id=resolved_tool_call_id,
                )
            )
            if decision.allowed is True and output["permissionDecision"] == "allow":
                await publish_agent_candidates(
                    agent_event_adapter.accept_hook(
                        "PreToolUse", hook_input, tool_use_id=resolved_tool_call_id
                    )
                )
        if decision.allowed is True and output["permissionDecision"] == "allow" and hook_input:
            tool_name = str(hook_input.get("tool_name") or "")
            identity = adapter_identity(tool_name)
            resolved_tool_call_id = exact_hook_tool_call_id(hook_input, tool_use_id)
            capability_evidence_acknowledged = True
            if tool_name.lower() != "skill" and not identity.startswith("mcp__"):
                lifecycle_acknowledged = await record_tool_lifecycle(
                    tool_name=tool_name,
                    tool_call_id=resolved_tool_call_id,
                    lifecycle="started",
                )
                if (
                    ("builtin", identity) in required_builtin_declarations
                    or identity in strict_tool_lifecycle_names
                ):
                    capability_evidence_acknowledged = lifecycle_acknowledged
            if tool_name.lower() == "skill":
                for skill_name in _extract_skill_names_from_tool_input(
                    hook_input.get("tool_input"),
                    allowed_skill_names,
                ):
                    capability_evidence_acknowledged = await record_capability_evidence(
                        capability_kind="skill",
                        canonical_identity=skill_name,
                        tool_call_id=resolved_tool_call_id,
                        lifecycle_phase="invocation_requested",
                    )
                    if capability_evidence_acknowledged is not True:
                        break
            elif identity in internal_context_subjects:
                capability_evidence_acknowledged = await record_tool_lifecycle(
                    tool_name="MCP",
                    tool_call_id=resolved_tool_call_id,
                    lifecycle="started",
                )
            elif identity in authorized_subjects and identity.startswith("mcp__"):
                capability_evidence_acknowledged = await record_capability_evidence(
                    capability_kind="mcp",
                    canonical_identity=identity,
                    tool_call_id=resolved_tool_call_id,
                    lifecycle_phase="invocation_requested",
                )
            if capability_evidence_acknowledged is not True:
                diagnostic_counters["tool_admission_denials"] += 1
                output["permissionDecision"] = "deny"
                output["permissionDecisionReason"] = (
                    "required_tool_completion_evidence_mismatch"
                )
        return {"hookSpecificOutput": output}
    def skill_tool_hook(lifecycle_phase: str):
        async def handler(hook_input, tool_use_id=None, _context=None) -> dict[str, object]:
            hook_input = hook_input if isinstance(hook_input, dict) else {}
            if str(hook_input.get("tool_name") or "").lower() != "skill":
                return {}
            call_id = exact_hook_tool_call_id(hook_input, tool_use_id)
            for skill_name in _extract_skill_names_from_tool_input(hook_input.get("tool_input"), allowed_skill_names):
                if lifecycle_phase == "failed" and skill_name not in failed_skill_names:
                    failed_skill_names.append(skill_name)
                await record_capability_evidence(
                    capability_kind="skill", canonical_identity=skill_name,
                    tool_call_id=call_id, lifecycle_phase=lifecycle_phase,
                    skill_metadata={
                        "source": "claude_agent_sdk_hook",
                        "hook_event_name": str(hook_input.get("hook_event_name") or ""),
                        "tool_name": "Skill", "tool_use_id": call_id,
                    } if lifecycle_phase == "completed" else None,
                )
            if agent_event_adapter is not None:
                await publish_agent_candidates(
                    agent_event_adapter.accept_hook(
                        "PostToolUseFailure" if lifecycle_phase == "failed" else "PostToolUse",
                        hook_input,
                        tool_use_id=call_id,
                    )
                )
            return {}
        return handler

    def mcp_tool_hook(lifecycle_phase: str):
        async def handler(hook_input, tool_use_id=None, _context=None) -> dict[str, object]:
            hook_input = hook_input if isinstance(hook_input, dict) else {}
            identity = adapter_identity(hook_input.get("tool_name"))
            if identity in internal_context_subjects:
                await record_tool_lifecycle(
                    tool_name="MCP",
                    tool_call_id=exact_hook_tool_call_id(hook_input, tool_use_id),
                    lifecycle=lifecycle_phase,
                )
            elif identity.startswith("mcp__") and identity in authorized_subjects:
                await record_capability_evidence(
                    capability_kind="mcp", canonical_identity=identity,
                    tool_call_id=exact_hook_tool_call_id(hook_input, tool_use_id),
                    lifecycle_phase=lifecycle_phase,
                )
            if agent_event_adapter is not None:
                await publish_agent_candidates(
                    agent_event_adapter.accept_hook(
                        "PostToolUseFailure" if lifecycle_phase == "failed" else "PostToolUse",
                        hook_input,
                        tool_use_id=exact_hook_tool_call_id(hook_input, tool_use_id),
                    )
                )
            return {}
        return handler

    def generic_tool_lifecycle_hook(lifecycle: str):
        async def handler(hook_input, tool_use_id=None, _context=None) -> dict[str, object]:
            hook_input = hook_input if isinstance(hook_input, dict) else {}
            tool_name = str(hook_input.get("tool_name") or "")
            if tool_name.lower() != "skill" and not adapter_identity(tool_name).startswith("mcp__"):
                await record_tool_lifecycle(
                    tool_name=tool_name,
                    tool_call_id=exact_hook_tool_call_id(hook_input, tool_use_id),
                    lifecycle=lifecycle,
                )
            if agent_event_adapter is not None:
                await publish_agent_candidates(
                    agent_event_adapter.accept_hook(
                        "PostToolUseFailure" if lifecycle == "failed" else "PostToolUse",
                        hook_input,
                        tool_use_id=exact_hook_tool_call_id(hook_input, tool_use_id),
                    )
                )
            return {}
        return handler

    try:
        _scrub_project_setting_files(cwd)
    except OSError:
        error_code = _SDK_TOOL_ADMISSION_FAILED
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            error=error_code,
            turn_diagnostics=turn_diagnostics(error_code),
        )

    hooks = None
    if HookMatcher is not None:
        hooks = {
            "PreToolUse": [
                HookMatcher(
                    matcher=None,
                    hooks=[enforce_side_effect_tool_policy],
                )
            ],
        }
        post_tool_hooks = []
        post_tool_failure_hooks = []
        if configured_skills:
            post_tool_hooks.append(HookMatcher(matcher="Skill", hooks=[skill_tool_hook("completed")]))
            post_tool_failure_hooks.append(
                HookMatcher(matcher="Skill", hooks=[skill_tool_hook("failed")])
            )
        if any(identity.startswith("mcp__") for identity in authorized_subjects) or internal_context_subjects:
            post_tool_hooks.append(HookMatcher(matcher="mcp__*", hooks=[mcp_tool_hook("completed")]))
            post_tool_failure_hooks.append(
                HookMatcher(matcher="mcp__*", hooks=[mcp_tool_hook("failed")])
            )
        post_tool_hooks.append(HookMatcher(matcher=None, hooks=[generic_tool_lifecycle_hook("completed")]))
        post_tool_failure_hooks.append(HookMatcher(matcher=None, hooks=[generic_tool_lifecycle_hook("failed")]))
        if post_tool_hooks:
            hooks["PostToolUse"] = post_tool_hooks
            hooks["PostToolUseFailure"] = post_tool_failure_hooks

    sdk_tools = (
        [identity for identity in authorized_subjects if not identity.startswith("mcp__")]
        if sandbox_brokered
        else _sdk_tools_for_mode(
            full_access=full_access,
            include_skill=bool(allowed_skill_names),
        )
    )
    # The installed SDK's SystemPromptPreset preserves Claude Code's default
    # system prompt while adding only server-owned profile instructions.
    sdk_system_prompt: dict[str, str] = {"type": "preset", "preset": "claude_code"}
    if system_prompt:
        sdk_system_prompt["append"] = system_prompt
    options = ClaudeAgentOptions(
        cwd=str(cwd),
        model=model_id or settings.claude_agent_model or settings.anthropic_model or None,
        system_prompt=sdk_system_prompt,
        tools=sdk_tools,
        mcp_servers=mcp_servers,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        env=build_sdk_env(cwd=cwd),
        skills=configured_skills,
        session_id=session_id,
        max_turns=max_turns,
        max_thinking_tokens=max(1, int(getattr(settings, "claude_agent_sdk_max_thinking_tokens", 16384))),
        effort=str(getattr(settings, "claude_agent_sdk_effort", "xhigh") or "xhigh"),
        can_use_tool=can_use_tool,
        hooks=hooks,
        include_partial_messages=sandbox_partial_streaming,
        setting_sources=["project"],
    )

    structured_result_text = ""
    result_session_id: str | None = None
    usage: dict[str, Any] = {}
    terminal_reason: str | None = None
    received_structured_terminal = False
    answer_text_limit = _MAX_REQUIRED_ANSWER_TEXT_CHARS
    projector_limits = (
        {"trailing_chars": answer_text_limit, "max_pending_chars": answer_text_limit}
        if required_answer_gate and not sandbox_tool_lifecycle_governed
        else {}
    )
    stream_projector = (
        ClaudeStreamProjector(sanitizer=sanitize_public_payload, **projector_limits)
        if sandbox_partial_streaming and not sandbox_tool_lifecycle_governed
        else None
    )

    def capability_completion_error() -> str | None:
        """Validate every observed call and every explicit requirement together."""

        if capability_evidence_rejected:
            return "required_tool_completion_evidence_mismatch"
        if governed_builtin_lifecycle_rejected:
            return "required_tool_completion_evidence_mismatch"
        groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
        call_owners: dict[str, tuple[str, str]] = {}
        for item in capability_evidence:
            key = (item.get("capability_kind"), item.get("canonical_identity"))
            call_id = item.get("tool_call_id")
            if key not in capability_plan.available or not call_id:
                return "required_tool_completion_evidence_mismatch"
            if call_owners.setdefault(call_id, key) != key:
                return "required_tool_completion_evidence_mismatch"
            groups.setdefault((*key, call_id), []).append(item)
        for (kind, identity, _call_id), matching in groups.items():
            declaration_sha256 = RequiredCapabilityDeclaration.from_authorized_subject(
                capability_kind=kind, canonical_identity=identity
            ).declaration_sha256
            if len(matching) != 2 or [item.get("lifecycle_phase") for item in matching] != ["invocation_requested", "completed"] or [item.get("lifecycle_status") for item in matching] != ["invoking", "succeeded"] or matching[0].get("tool_call_id") != matching[1].get("tool_call_id") or any(item.get("declaration_sha256") != declaration_sha256 for item in matching):
                return "required_tool_completion_evidence_mismatch"
        for key in required_capability_declarations:
            matches = sum(group[:2] == key for group in groups)
            if not matches:
                return "required_tool_completion_evidence_missing"
            if matches != 1:
                return "required_tool_completion_evidence_mismatch"
        for key in required_builtin_declarations:
            identity = key[1]
            matching_states = {
                state
                for (tool_name, _call_id), state in governed_builtin_invocation_states.items()
                if tool_name == identity
            }
            if "started" in matching_states:
                return "required_tool_completion_evidence_missing"
            if "completed" not in matching_states:
                return "required_tool_completion_evidence_missing"
        governed_tool_states = set(governed_builtin_invocation_states.values())
        if "started" in governed_tool_states:
            return "required_tool_completion_evidence_missing"
        if not governed_tool_states <= {"completed", "failed"}:
            return "required_tool_completion_evidence_mismatch"
        if actual_mcp_invocation_observed and not any(group[0] == "mcp" for group in groups):
            return "required_tool_completion_evidence_mismatch"
        return None

    async def publish_terminal_text(value: str, *, project_agent: bool = True) -> bool:
        if not value:
            return True
        if project_agent and agent_event_adapter is not None:
            for offset in range(0, len(value), 8_192):
                acknowledged = await publish_agent_candidates(
                    agent_event_adapter.accept_answer_text(
                        value[offset : offset + 8_192],
                        already_gated=True,
                    )
                )
                if not acknowledged:
                    return False
        if on_text is None:
            return True
        callback_result = on_text(value)
        if isawaitable(callback_result):
            await callback_result
        return True

    async def consume() -> ClaudeAgentSdkRunResult:
        nonlocal result_session_id, usage, terminal_reason, received_structured_terminal
        nonlocal last_public_stage, structured_result_text
        async for message in query(
            prompt=_sdk_user_prompt_stream(
                sdk_prompt,
                session_id=session_id,
            ),
            options=options,
        ):
            if agent_event_adapter is not None and isinstance(
                message,
                (TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage, TaskUpdatedMessage),
            ):
                await publish_agent_candidates(agent_event_adapter.accept_task_message(message))
                continue
            if stream_projector is not None and isinstance(message, StreamEvent):
                for text in stream_projector.accept(message.event):
                    for public_text in answer_stream_gate.accept(text):
                        await publish_terminal_text(public_text)
                continue
            if isinstance(message, AssistantMessage):
                diagnostic_counters["assistant_messages"] += 1
                assistant_text_blocks = []
                for block_index, block in enumerate(message.content):
                    if agent_event_adapter is not None:
                        await publish_agent_candidates(
                            agent_event_adapter.accept_content_block(
                                block,
                                block_index=block_index,
                                message_identity=(
                                    getattr(message, "message_id", None)
                                    or getattr(message, "uuid", None)
                                    or getattr(message, "session_id", None)
                                ),
                            )
                        )
                    if isinstance(block, TextBlock):
                        diagnostic_counters["text_blocks"] += 1
                        last_public_stage = "message"
                        text = getattr(block, "text", "")
                        assistant_text_blocks.append(text)
                if (
                    required_answer_gate
                    or actual_mcp_invocation_observed
                    or actual_sandbox_bash_invocation_observed
                ) and (
                    stream_projector is None or not stream_projector.partial_emitted
                ):
                    assistant_text = (
                        "".join(assistant_text_blocks)
                        if all(isinstance(text, str) for text in assistant_text_blocks)
                        else None
                    )
                    for public_text in answer_stream_gate.accept(assistant_text):
                        await publish_terminal_text(public_text)
            elif isinstance(message, ResultMessage):
                diagnostic_counters["result_messages"] += 1
                diagnostic_counters["turns_observed"] = _bounded_diagnostic_counter(
                    getattr(message, "num_turns", 0)
                )
                permission_denials = getattr(message, "permission_denials", None)
                if isinstance(permission_denials, list):
                    diagnostic_counters["tool_admission_denials"] += len(permission_denials)
                result_session_id = message.session_id
                usage = message.usage or message.model_usage or {}
                sdk_terminal_reason = getattr(message, "terminal_reason", None)
                resolved_terminal_reason = (
                    str(sdk_terminal_reason).strip()
                    if isinstance(sdk_terminal_reason, str) and sdk_terminal_reason.strip()
                    else None
                )
                if message.is_error:
                    answer_stream_gate.finish(final_text="", release=False)
                    seal_agent_candidates("result_error")
                    raw_error = (
                        "; ".join(message.errors or [])
                        or message.stop_reason
                        or getattr(message, "subtype", "")
                        or "claude_agent_sdk_error"
                    )
                    error_code = _canonical_sdk_error(
                        raw_error,
                        result_subtype=getattr(message, "subtype", ""),
                        stop_reason=getattr(message, "stop_reason", ""),
                        terminal_reason=resolved_terminal_reason,
                        selected_skill_error=selected_skill_hook_error(),
                        tool_admission_denials=diagnostic_counters["tool_admission_denials"],
                    )
                    return ClaudeAgentSdkRunResult(
                        used_sdk=True,
                        message="",
                        session_id=result_session_id,
                        usage=usage,
                        error=error_code,
                        terminal_reason=resolved_terminal_reason,
                        used_skills=list(used_skill_names),
                        used_skills_source="executor_hook" if used_skill_names else "",
                        turn_diagnostics=turn_diagnostics(error_code),
                        capability_evidence=list(capability_evidence),
                    )
                abnormal_terminal_error = _canonical_sdk_error(
                    "",
                    terminal_reason=resolved_terminal_reason,
                ) if resolved_terminal_reason in {
                    "max_turns",
                    "max_turns_exceeded",
                    "aborted_streaming",
                    "aborted_tools",
                    "cancelled",
                    "canceled",
                } else None
                if abnormal_terminal_error is not None:
                    answer_stream_gate.finish(final_text="", release=False)
                    seal_agent_candidates("abnormal_terminal")
                    return ClaudeAgentSdkRunResult(
                        used_sdk=True,
                        message="",
                        session_id=result_session_id,
                        usage=usage,
                        error=abnormal_terminal_error,
                        terminal_reason=resolved_terminal_reason,
                        received_structured_terminal=False,
                        used_skills=list(used_skill_names),
                        used_skills_source="executor_hook" if used_skill_names else "",
                        turn_diagnostics=turn_diagnostics(abnormal_terminal_error),
                        capability_evidence=list(capability_evidence),
                    )
                received_structured_terminal = True
                structured_result_text = str(message.result or "").strip()
                stop_reason = getattr(message, "stop_reason", None)
                terminal_reason = resolved_terminal_reason or (
                    str(stop_reason).strip() if isinstance(stop_reason, str) and stop_reason.strip() else None
                )
                break
        if stream_projector is not None:
            stream_projector.close_unfinished()
            if stream_projector.disabled:
                answer_stream_gate.fail_closed()
        terminal_error = _SDK_MISSING_STRUCTURED_TERMINAL if not received_structured_terminal else None
        if terminal_error is None and agent_event_callback_failed:
            terminal_error = "agent_event_callback_not_acknowledged"
        if terminal_error is None and capability_evidence_rejected:
            terminal_error = "required_tool_completion_evidence_mismatch"
        if terminal_error is None:
            terminal_error = selected_skill_hook_error()
        if terminal_error is None:
            terminal_error = capability_completion_error()
        if terminal_error is None and answer_stream_gate.final_text_exceeds_bound(
            structured_result_text
        ):
            terminal_error = _SDK_TOOL_ADMISSION_FAILED
        finished_answer = answer_stream_gate.finish(
            final_text=structured_result_text,
            release=terminal_error is None,
        )
        if terminal_error is None and answer_stream_gate.failed:
            terminal_error = _SDK_TOOL_ADMISSION_FAILED
        if terminal_error is None and isinstance(message, ResultMessage):
            terminal_candidates: list[Any] = []
            for public_text in finished_answer.chunks:
                for offset in range(0, len(public_text), _MAX_PUBLIC_DELTA_CHARS):
                    terminal_candidates.extend(
                        agent_event_adapter.accept_answer_text(
                            public_text[offset : offset + _MAX_PUBLIC_DELTA_CHARS],
                            already_gated=True,
                        )
                        if agent_event_adapter is not None
                        else ()
                    )
            if agent_event_adapter is not None:
                terminal_candidates.extend(
                    agent_event_adapter.accept_result(
                        message,
                        final_content=finished_answer.final_text,
                    )
                )
                if not await publish_agent_candidates(tuple(terminal_candidates)):
                    terminal_error = "agent_event_callback_not_acknowledged"
            if terminal_error is None and on_text is not None and finished_answer.final_text:
                callback_result = on_text(finished_answer.final_text)
                if isawaitable(callback_result):
                    await callback_result
        if terminal_error is not None:
            seal_agent_candidates(terminal_error)
        public_structured_result_text = (
            finished_answer.final_text if terminal_error is None else ""
        )
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            message=public_structured_result_text,
            session_id=result_session_id,
            usage=usage,
            error=terminal_error,
            terminal_reason=terminal_reason,
            received_structured_terminal=received_structured_terminal,
            used_skills=list(used_skill_names),
            used_skills_source="executor_hook" if used_skill_names else "",
            turn_diagnostics=turn_diagnostics(terminal_error),
            capability_evidence=list(capability_evidence),
        )

    consume_cancellation: asyncio.CancelledError | None = None

    async def consume_with_cancellation_identity() -> ClaudeAgentSdkRunResult:
        nonlocal consume_cancellation
        try:
            return await consume()
        except asyncio.CancelledError as exc:
            consume_cancellation = exc
            raise

    consume_task = asyncio.create_task(consume_with_cancellation_identity())
    try:
        return await asyncio.wait_for(asyncio.shield(consume_task), timeout=timeout_seconds)
    except asyncio.CancelledError:
        seal_agent_candidates("cancelled")
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        if consume_cancellation is not None and type(consume_cancellation) is not asyncio.CancelledError:
            raise consume_cancellation
        raise
    except TimeoutError:
        seal_agent_candidates("timeout")
        consume_task.cancel()
        try:
            await consume_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        error_code = _SDK_TIMEOUT
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            message="",
            session_id=result_session_id,
            usage=usage,
            error=error_code,
            used_skills=list(used_skill_names),
            used_skills_source="executor_hook" if used_skill_names else "",
            turn_diagnostics=turn_diagnostics(error_code),
            capability_evidence=list(capability_evidence),
        )
    except Exception as exc:  # noqa: BLE001
        seal_agent_candidates("exception")
        error_code = _canonical_sdk_error(
            exc,
            selected_skill_error=selected_skill_hook_error(),
            tool_admission_denials=diagnostic_counters["tool_admission_denials"],
        )
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            message="",
            session_id=result_session_id,
            usage=usage,
            error=error_code,
            used_skills=list(used_skill_names),
            used_skills_source="executor_hook" if used_skill_names else "",
            turn_diagnostics=turn_diagnostics(error_code),
            capability_evidence=list(capability_evidence),
        )
