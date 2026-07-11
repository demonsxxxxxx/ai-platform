import asyncio
import json
import os
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable

from app.context_retrieval import ContextRetrieval, ContextRetrievalDenied
from app.control_plane_contracts import sanitize_public_payload
from app.public_context_keys import safe_public_context_pack_version
from app.settings import get_settings

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

_SDK_BASE_AVAILABLE_TOOLS = ["Read", "Glob", "LS", "Bash"]
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
_SDK_PROJECT_SETTING_FILES = (".claude/settings.json", ".claude/settings.local.json")
_SDK_FULL_ACCESS_MIN_TIMEOUT_SECONDS = 1800.0
_SHELL_UNSAFE_CHARS = set("$`;&|<>{}[]*?!\n\r")
_QA_REVIEW_PREFLIGHT_LS_FLAGS = {"-l", "-la", "-al"}
_QA_REVIEW_PREFLIGHT_LS_PATHS = (
    ".claude/skills/minimax-docx/docx_engine.py",
    ".claude/skills/qa-file-reviewer/scripts/run_qa_review.py",
)
_TRANSLATION_TARGET_ALIASES = {
    "english": "English",
    "英文": "English",
    "en": "English",
    "chinese": "Chinese",
    "中文": "Chinese",
    "zh": "Chinese",
}
_ALLOWED_TRANSLATION_TARGETS = frozenset(_TRANSLATION_TARGET_ALIASES.values())


@dataclass(frozen=True)
class ClaudeAgentSdkRunResult:
    used_sdk: bool
    message: str = ""
    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    used_skills: list[str] = field(default_factory=list)
    used_skills_source: str = ""


class ClaudeAgentSdkNotAvailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ScopedContextRetrievalIdentity:
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    run_id: str
    agent_id: str


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _path_inside(base: Path, value: str) -> bool:
    if not value:
        return False
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        candidate.resolve(strict=False).relative_to(base.resolve(strict=False))
    except ValueError:
        return False
    return True


def _path_equals(base: Path, value: str, expected: Path) -> bool:
    if not value:
        return False
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False) == expected.resolve(strict=False)


def _canonical_inside_path(base: Path, value: str) -> Path | None:
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(base.resolve(strict=False))
    except ValueError:
        return None
    return resolved


def _contains_shell_expansion(value: str) -> bool:
    return any(char in value for char in _SHELL_UNSAFE_CHARS)


def _shell_segments(command: str) -> list[list[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    if not tokens:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "&&":
            if not segments[-1]:
                return []
            segments.append([])
            continue
        if token in {";", "|", "||", "&", ">", ">>", "<", "2>", "2>>"}:
            return []
        segments[-1].append(token)
    if not segments[-1]:
        return []
    return segments


def _canonical_output_mkdir(segment: list[str], cwd: Path) -> list[str] | None:
    if len(segment) != 3 or segment[0] != "mkdir" or segment[1] != "-p":
        return None
    if _contains_shell_expansion(segment[2]) or not _path_equals(cwd, segment[2], cwd / "output"):
        return None
    return ["mkdir", "-p", str((cwd / "output").resolve(strict=False))]


def _canonical_qa_review_runner(segment: list[str], cwd: Path) -> list[str] | None:
    if segment and segment[-1] == "2>&1":
        segment = segment[:-1]
    if len(segment) != 7:
        return None
    if segment[0] not in {"python", "python3"}:
        return None
    expected_script = cwd / ".claude" / "skills" / "qa-file-reviewer" / "scripts" / "run_qa_review.py"
    if any(_contains_shell_expansion(value) for value in segment):
        return None
    if not _path_equals(cwd, segment[1], expected_script):
        return None
    input_path = _canonical_inside_path(cwd, segment[2])
    if input_path is None or input_path.suffix.lower() != ".docx":
        return None
    if not _path_equals(cwd, segment[3], cwd / "output"):
        return None
    if segment[4] != "--with-comments" or segment[5] != "--original-filename":
        return None
    original_name = segment[6]
    if "/" in original_name or "\\" in original_name or Path(original_name).name != original_name:
        return None
    return [
        segment[0],
        str(expected_script.resolve(strict=False)),
        str(input_path),
        str((cwd / "output").resolve(strict=False)),
        "--with-comments",
        "--original-filename",
        original_name,
    ]


def _canonical_baoyu_translate_runner(segment: list[str], cwd: Path) -> list[str] | None:
    if segment and segment[-1] == "2>&1":
        segment = segment[:-1]
    if len(segment) != 8:
        return None
    if segment[0] not in {"python", "python3"}:
        return None
    expected_script = cwd / ".claude" / "skills" / "baoyu-translate" / "scripts" / "run_translation.py"
    if any(_contains_shell_expansion(value) for value in segment):
        return None
    if not _path_equals(cwd, segment[1], expected_script):
        return None
    input_path = _canonical_inside_path(cwd, segment[2])
    if input_path is None or input_path.suffix.lower() != ".docx":
        return None
    if not _path_equals(cwd, segment[3], cwd / "output"):
        return None
    if segment[4] != "--target-language" or segment[5] not in _ALLOWED_TRANSLATION_TARGETS:
        return None
    if segment[6] != "--original-filename":
        return None
    original_name = segment[7]
    if "/" in original_name or "\\" in original_name or Path(original_name).name != original_name:
        return None
    return [
        segment[0],
        str(expected_script.resolve(strict=False)),
        str(input_path),
        str((cwd / "output").resolve(strict=False)),
        "--target-language",
        segment[5],
        "--original-filename",
        original_name,
    ]


def _canonical_qa_review_preflight_ls(segment: list[str], cwd: Path) -> list[str] | None:
    if not segment or segment[0] != "ls":
        return None
    remaining = segment[1:]
    flags: list[str] = []
    if remaining and remaining[0].startswith("-"):
        if remaining[0] not in _QA_REVIEW_PREFLIGHT_LS_FLAGS:
            return None
        flags = [remaining[0]]
        remaining = remaining[1:]
    if not remaining:
        return None
    expected_paths = [cwd / relative_path for relative_path in _QA_REVIEW_PREFLIGHT_LS_PATHS]
    canonical_paths: list[str] = []
    for value in remaining:
        if _contains_shell_expansion(value):
            return None
        matched_path = None
        for expected in expected_paths:
            if _path_equals(cwd, value, expected):
                matched_path = str(expected.resolve(strict=False))
                break
        if matched_path is None:
            return None
        if matched_path not in canonical_paths:
            canonical_paths.append(matched_path)
    return ["ls", *flags, *canonical_paths]


def _canonical_permitted_bash_command_with_kind(command: str, cwd: Path) -> tuple[str, str] | None:
    segments = _shell_segments(command)
    canonical_segments: list[list[str]] = []
    command_kind = ""
    if len(segments) == 1:
        mkdir = _canonical_output_mkdir(segments[0], cwd)
        runner = _canonical_qa_review_runner(segments[0], cwd)
        translate_runner = _canonical_baoyu_translate_runner(segments[0], cwd)
        preflight_ls = _canonical_qa_review_preflight_ls(segments[0], cwd)
        if mkdir:
            canonical_segments = [mkdir]
            command_kind = "qa_review_preflight"
        elif runner:
            canonical_segments = [runner]
            command_kind = "qa_review_runner"
        elif translate_runner:
            canonical_segments = [translate_runner]
            command_kind = "baoyu_translate_runner"
        elif preflight_ls:
            canonical_segments = [preflight_ls]
            command_kind = "qa_review_preflight"
    elif len(segments) == 2:
        mkdir = _canonical_output_mkdir(segments[0], cwd)
        runner = _canonical_qa_review_runner(segments[1], cwd)
        translate_runner = _canonical_baoyu_translate_runner(segments[1], cwd)
        if mkdir and runner:
            canonical_segments = [mkdir, runner]
            command_kind = "qa_review_runner"
        elif mkdir and translate_runner:
            canonical_segments = [mkdir, translate_runner]
            command_kind = "baoyu_translate_runner"
    if not canonical_segments:
        return None
    return " && ".join(shlex.join(segment) for segment in canonical_segments), command_kind


def _canonical_permitted_bash_command(command: str, cwd: Path) -> str | None:
    result = _canonical_permitted_bash_command_with_kind(command, cwd)
    return result[0] if result else None


def _is_permitted_bash_command(command: str, cwd: Path) -> bool:
    return _canonical_permitted_bash_command(command, cwd) is not None


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


def _sdk_tools_for_mode(*, full_access: bool = False) -> list[str]:
    if full_access:
        return list(_SDK_AVAILABLE_TOOLS)
    return list(_SDK_BASE_AVAILABLE_TOOLS)


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
    return env


def _quote_bash_arg(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def _translation_target_language(user_message: str) -> str:
    lowered = user_message.casefold()
    for token, target in _TRANSLATION_TARGET_ALIASES.items():
        if token.casefold() in lowered:
            return target
    return "English"


def _controlled_fast_path_instruction(*, skill_id: str, user_message: str, file_names: list[str]) -> str:
    docx_name = next((name for name in file_names if str(name).lower().endswith(".docx")), "")
    if not docx_name:
        return ""
    quoted_name = _quote_bash_arg(str(docx_name))
    if skill_id == "baoyu-translate":
        target_language = _quote_bash_arg(_translation_target_language(user_message))
        command = (
            "mkdir -p output && python .claude/skills/baoyu-translate/scripts/run_translation.py "
            f"{quoted_name} output --target-language {target_language} --original-filename {quoted_name}"
        )
        return (
            "\n\nControlled fast path for this file task:\n"
            f"- Run this exact command before reading staged skill files:\n  {command}\n"
            "- Do not list or read staged skill files before running this command.\n"
            "- Use relative filenames from the current working directory and save artifacts under output/."
        )
    if skill_id != "qa-file-reviewer":
        return ""
    command = (
        "mkdir -p output && python .claude/skills/qa-file-reviewer/scripts/run_qa_review.py "
        f"{quoted_name} output --with-comments --original-filename {quoted_name}"
    )
    return (
        "\n\nControlled fast path for this file task:\n"
        f"- Run this exact command before reading staged skill files:\n  {command}\n"
        "- Do not list or read staged skill files before running this command.\n"
        "- Use relative filenames from the current working directory and save artifacts under output/."
    )


def _context_pack_prompt_section(context_pack: dict[str, Any] | None) -> str:
    if not isinstance(context_pack, dict):
        return ""
    if context_pack.get("schema_version") != "ai-platform.executor-context-pack.v1":
        return ""
    prompt_summary = context_pack.get("prompt_summary")
    if not isinstance(prompt_summary, str):
        return ""
    prompt_summary = prompt_summary.strip()
    if not prompt_summary:
        return ""
    if sanitize_public_payload(prompt_summary) != prompt_summary:
        return ""
    metadata_lines: list[str] = []
    context_pack_version = _safe_context_pack_version(context_pack.get("context_pack_version"))
    if context_pack_version:
        metadata_lines.append(f"- Context pack version: {context_pack_version}")
    context_pack_generated_at = _safe_context_pack_generated_at(
        context_pack.get("context_pack_generated_at")
    )
    if context_pack_generated_at:
        metadata_lines.append(f"- Context pack generated at: {context_pack_generated_at}")
    manifest = context_pack.get("context_manifest")
    if isinstance(manifest, dict) and manifest.get("schema_version") == "ai-platform.context-manifest.v1":
        message_count = len(manifest.get("recent_messages") or [])
        file_count = len(manifest.get("files") or [])
        artifact_count = len(manifest.get("artifacts") or [])
        memory_count = len(manifest.get("memory_records") or [])
        metadata_lines.append(
            "- Context manifest refs: "
            f"{message_count} message(s), {file_count} file(s), "
            f"{artifact_count} artifact(s), {memory_count} memory record(s)"
        )
        tools = manifest.get("available_retrieval_tools")
        if isinstance(tools, list):
            safe_tools = [
                str(tool)
                for tool in tools
                if str(tool) in {
                    "read_session_messages",
                    "read_context_file",
                    "read_run_artifact",
                    "stage_context_file_to_workspace",
                    "search_memory",
                }
            ]
            if safe_tools:
                metadata_lines.append(f"- Available context retrieval tools: {', '.join(safe_tools)}")
    metadata_text = "\n".join(metadata_lines)
    if metadata_text:
        metadata_text += "\n"
    return (
        "\n\nOffice context pack:\n"
        f"- {prompt_summary}\n"
        f"{metadata_text}"
        "- Use this bounded context only as background; do not infer raw storage keys, "
        "sandbox paths, private payloads, or long-term memory beyond what is listed.\n"
        "- Use context retrieval tools before assuming full prior message, file, artifact, or memory content is available."
    )


def _safe_context_pack_version(value: object) -> str:
    return safe_public_context_pack_version(value) or ""


def _safe_context_pack_generated_at(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if sanitize_public_payload(text) != text:
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return text


def build_skill_prompt(
    *,
    skill_id: str,
    user_message: str,
    file_names: list[str],
    context_pack: dict[str, Any] | None = None,
) -> str:
    files_text = "\n".join(f"- {name}" for name in file_names) if file_names else "- no files"
    return (
        "You are running inside the ai-platform controlled worker. "
        "Use only backend-managed skills staged in this workspace and do not access arbitrary shell, SQL, or host filesystem paths.\n\n"
        f"User request: {user_message}\n"
        f"Workspace files:\n{files_text}\n\n"
        "If a staged Skill matches the task, use that Skill's instructions. "
        "Return a concise execution summary and ensure generated artifacts are saved in the workspace output directory."
        f"{_context_pack_prompt_section(context_pack)}"
        f"{_controlled_fast_path_instruction(skill_id=skill_id, user_message=user_message, file_names=file_names)}"
    )


async def _sdk_user_prompt_stream(prompt: str, *, session_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": session_id or "default",
    }


def _append_result_text(texts: list[str], result: str | None) -> None:
    result_text = (result or "").strip()
    if not result_text:
        return
    current_text = "\n".join(texts).strip()
    if result_text == current_text or current_text.endswith(result_text):
        return
    if current_text and result_text.startswith(current_text):
        texts[:] = [result_text]
        return
    texts.append(result_text)


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


def _extract_skill_names_from_tool_input(tool_input: Any, allowed_skill_names: set[str]) -> list[str]:
    candidates: list[str] = []
    _append_skill_candidate(candidates, tool_input)
    names: list[str] = []
    for candidate in candidates:
        if allowed_skill_names and candidate not in allowed_skill_names:
            continue
        if candidate not in names:
            names.append(candidate)
    return names


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


def _safe_positive_int(value: object, *, default: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(1, min(maximum, normalized))


def _safe_non_negative_int(value: object, *, default: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(0, min(maximum, normalized))


def _build_context_retrieval_mcp_server(
    sdk: object,
    *,
    retrieval: ContextRetrieval | None,
    identity: ScopedContextRetrievalIdentity | None,
    workspace_root: Path,
):
    if retrieval is None or identity is None:
        return None
    sdk_tool = getattr(sdk, "tool", None)
    create_server = getattr(sdk, "create_sdk_mcp_server", None)
    if sdk_tool is None or create_server is None:
        return None

    async def _run(action, args: dict[str, Any], *, audit_action: str = "context_retrieval.tool") -> dict[str, Any]:
        try:
            return _context_retrieval_tool_response(await action(args))
        except ContextRetrievalDenied as exc:
            reason = str(exc) or "context_scope_denied"
            if reason not in {"context_file_too_large", "context_file_size_required"}:
                reason = "context_scope_denied"
            return _context_retrieval_tool_error(reason, action=audit_action)
        except Exception:
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
        return await _run(
            lambda tool_args: retrieval.read_session_messages(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                run_id=identity.run_id,
                limit=_safe_positive_int(tool_args.get("limit"), default=20, maximum=100),
                offset=_safe_non_negative_int(tool_args.get("offset"), default=0, maximum=10000),
                max_tokens=_safe_positive_int(tool_args.get("max_tokens"), default=1200, maximum=8000),
            ),
            args if isinstance(args, dict) else {},
            audit_action="context_retrieval.read_session_messages",
        )

    @sdk_tool(
        "read_context_file",
        "Read an uploaded context file for the current ai-platform run scope only.",
        {
            "file_id": str,
            "max_bytes": int,
        },
    )
    async def read_context_file(args):
        tool_args = args if isinstance(args, dict) else {}
        file_id = str(tool_args.get("file_id") or "")
        if not file_id:
            return _context_retrieval_tool_error("file_id_required")
        return await _run(
            lambda inner: retrieval.read_context_file(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                run_id=identity.run_id,
                file_id=file_id,
                max_bytes=_safe_positive_int(inner.get("max_bytes"), default=65536, maximum=262144),
            ),
            tool_args,
            audit_action="context_retrieval.read_context_file",
        )

    @sdk_tool(
        "read_run_artifact",
        "Read a run artifact for the current ai-platform run scope only.",
        {
            "artifact_id": str,
            "max_bytes": int,
        },
    )
    async def read_run_artifact(args):
        tool_args = args if isinstance(args, dict) else {}
        artifact_id = str(tool_args.get("artifact_id") or "")
        if not artifact_id:
            return _context_retrieval_tool_error("artifact_id_required")
        return await _run(
            lambda inner: retrieval.read_run_artifact(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                run_id=identity.run_id,
                artifact_id=artifact_id,
                max_bytes=_safe_positive_int(inner.get("max_bytes"), default=65536, maximum=262144),
            ),
            tool_args,
            audit_action="context_retrieval.read_run_artifact",
        )

    @sdk_tool(
        "stage_context_file_to_workspace",
        "Stage an uploaded context file into the current run workspace and return a workspace-relative path.",
        {
            "file_id": str,
            "max_bytes": int,
        },
    )
    async def stage_context_file_to_workspace(args):
        tool_args = args if isinstance(args, dict) else {}
        file_id = str(tool_args.get("file_id") or "")
        if not file_id:
            return _context_retrieval_tool_error("file_id_required")
        return await _run(
            lambda _inner: retrieval.stage_context_file_to_workspace(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                session_id=identity.session_id,
                run_id=identity.run_id,
                file_id=file_id,
                workspace_root=str(workspace_root),
                max_bytes=_safe_positive_int(tool_args.get("max_bytes"), default=1048576, maximum=1048576),
            ),
            tool_args,
            audit_action="context_retrieval.stage_context_file_to_workspace",
        )

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
        tool_args = args if isinstance(args, dict) else {}
        return await _run(
            lambda inner: retrieval.search_memory(
                tenant_id=identity.tenant_id,
                workspace_id=identity.workspace_id,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
                session_id=identity.session_id,
                query=str(inner.get("query") or ""),
                limit=_safe_positive_int(inner.get("limit"), default=10, maximum=50),
                max_tokens=_safe_positive_int(inner.get("max_tokens"), default=1200, maximum=8000),
            ),
            tool_args,
            audit_action="context_retrieval.search_memory",
        )

    return create_server(
        "ai-platform-context",
        version="1.0.0",
        tools=[
            read_session_messages,
            read_context_file,
            read_run_artifact,
            stage_context_file_to_workspace,
            search_memory,
        ],
    )


async def run_claude_agent_sdk(
    *,
    prompt: str,
    cwd: Path,
    skill_id: str,
    session_id: str | None = None,
    context_retrieval: ContextRetrieval | None = None,
    context_retrieval_identity: ScopedContextRetrievalIdentity | None = None,
    model_id: str | None = None,
    skills: list[str] | None = None,
    query_fn: Callable[..., Any] | None = None,
    on_text: Callable[[str], Awaitable[None]] | None = None,
    on_skill_use: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_permission: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    execution_policy: str = "worker_local_legacy",
) -> ClaudeAgentSdkRunResult:
    settings = get_settings()
    if not settings.claude_agent_sdk_enabled:
        return ClaudeAgentSdkRunResult(used_sdk=False, error="claude_agent_sdk_disabled")
    try:
        import claude_agent_sdk as sdk

        AssistantMessage = sdk.AssistantMessage
        ClaudeAgentOptions = sdk.ClaudeAgentOptions
        ResultMessage = sdk.ResultMessage
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
    configured_skills = skills if skills is not None else (_split_csv(settings.claude_agent_sdk_skills) or [skill_id])
    allowed_skill_names = set(configured_skills)
    used_skill_names: list[str] = []
    sandbox_brokered = execution_policy == "sandbox_brokered"
    full_access = _full_access_requested(settings) and not sandbox_brokered
    permission_mode = (
        "dontAsk"
        if sandbox_brokered
        else _sdk_permission_mode(
            getattr(settings, "claude_agent_permission_mode", "dontAsk"),
            full_access=full_access,
        )
    )
    allowed_tools = (
        ["Read", "Glob", "LS"]
        if sandbox_brokered
        else _safe_allowed_tools(
            getattr(settings, "claude_agent_allowed_tools", "Read,Glob,LS"),
            full_access=full_access,
        )
    )
    context_retrieval_server = _build_context_retrieval_mcp_server(
        sdk,
        retrieval=context_retrieval,
        identity=context_retrieval_identity,
        workspace_root=cwd,
    )
    if context_retrieval_server is not None and not sandbox_brokered:
        for tool_name in (
            "read_session_messages",
            "read_context_file",
            "read_run_artifact",
            "stage_context_file_to_workspace",
            "search_memory",
        ):
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
    timeout_seconds = float(getattr(settings, "claude_agent_sdk_timeout_seconds", 120.0))
    if full_access:
        timeout_seconds = max(timeout_seconds, _SDK_FULL_ACCESS_MIN_TIMEOUT_SECONDS)

    async def record_used_skill(skill_name: str, metadata: dict[str, Any]) -> None:
        if allowed_skill_names and skill_name not in allowed_skill_names:
            return
        if skill_name in used_skill_names:
            return
        used_skill_names.append(skill_name)
        if on_skill_use:
            await on_skill_use(skill_name, metadata)

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], _context=None):
        if sandbox_brokered:
            if tool_name in _SDK_LOCAL_READ_ONLY_TOOLS:
                return PermissionResultAllow()
            return PermissionResultDeny(message="Tool use requires the platform sandbox permission broker")
        if full_access and tool_name in _sdk_tools_for_mode(full_access=True):
            return PermissionResultAllow()
        if tool_name == "Bash" and isinstance(tool_input, dict):
            command = str(tool_input.get("command") or "")
            permitted_command = _canonical_permitted_bash_command(command, cwd)
            if permitted_command:
                return PermissionResultAllow(updated_input={**tool_input, "command": permitted_command})
        return PermissionResultDeny(message="Tool use is not permitted by ai-platform runner policy")

    async def enforce_side_effect_tool_policy(hook_input, tool_use_id=None, _context=None) -> dict[str, object]:
        reason = "Tool use is not permitted by ai-platform runner policy"
        if not isinstance(hook_input, dict):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        tool_name = str(hook_input.get("tool_name") or "")
        if sandbox_brokered and tool_name in _SDK_LOCAL_READ_ONLY_TOOLS:
            return {}
        brokered_tool = sandbox_brokered and bool(tool_name)
        if tool_name != "Bash" and not brokered_tool:
            return {}
        tool_input = hook_input.get("tool_input")
        if not isinstance(tool_input, dict):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        permitted = (
            _canonical_permitted_bash_command_with_kind(str(tool_input.get("command") or ""), cwd)
            if tool_name == "Bash"
            else None
        )
        if permitted and not sandbox_brokered:
            permitted_command, command_kind = permitted
            if command_kind == "qa_review_runner":
                await record_used_skill(
                    "qa-file-reviewer",
                    {
                        "source": "claude_agent_sdk_hook",
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_use_id": str(hook_input.get("tool_use_id") or tool_use_id or ""),
                    },
                )
            elif command_kind == "baoyu_translate_runner":
                await record_used_skill(
                    "baoyu-translate",
                    {
                        "source": "claude_agent_sdk_hook",
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_use_id": str(hook_input.get("tool_use_id") or tool_use_id or ""),
                    },
                )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": (
                        "ai-platform allowlisted QA review runner command"
                        if command_kind == "qa_review_runner"
                        else "ai-platform allowlisted Baoyu translate runner command"
                        if command_kind == "baoyu_translate_runner"
                        else "ai-platform allowlisted QA review preflight command"
                    ),
                    "updatedInput": {**tool_input, "command": permitted_command},
                }
            }
        if full_access:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "ai-platform full access permits Bash",
                }
            }
        if on_tool_permission is not None:
            try:
                permission = await on_tool_permission(
                    {
                        "tool_name": tool_name,
                        "tool_call_id": str(hook_input.get("tool_use_id") or tool_use_id or ""),
                        "tool_input_keys": sorted(str(key) for key in tool_input),
                        "risk_level": "high",
                        "write_capable": True,
                        "action": "execute",
                        "reason": f"Claude SDK requested {tool_name} through the platform sandbox permission broker",
                        "tool_input": tool_input,
                    }
                )
            except Exception:
                permission = {"allowed": False, "reason": "tool_permission_broker_failed"}
            if not isinstance(permission, dict):
                permission = {"allowed": False, "reason": "tool_permission_malformed_response"}
            permission_reason = str(permission.get("reason") or reason)
            output = {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if bool(permission.get("allowed")) else "deny",
                "permissionDecisionReason": permission_reason,
            }
            permission_request_id = str(permission.get("permission_request_id") or "")
            if permission_request_id:
                output["permission_request_id"] = permission_request_id
            if bool(permission.get("allowed")) and permitted:
                output["updatedInput"] = {**tool_input, "command": permitted[0]}
            return {"hookSpecificOutput": output}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def record_skill_tool_use(hook_input, tool_use_id=None, _context=None) -> dict[str, object]:
        if not isinstance(hook_input, dict):
            return {}
        tool_name = str(hook_input.get("tool_name") or "")
        if tool_name.lower() != "skill":
            return {}
        for skill_name in _extract_skill_names_from_tool_input(hook_input.get("tool_input"), allowed_skill_names):
            await record_used_skill(
                skill_name,
                {
                    "source": "claude_agent_sdk_hook",
                    "hook_event_name": str(hook_input.get("hook_event_name") or ""),
                    "tool_name": tool_name,
                    "tool_use_id": str(hook_input.get("tool_use_id") or tool_use_id or ""),
                },
            )
        return {}

    try:
        _scrub_project_setting_files(cwd)
    except OSError as exc:
        return ClaudeAgentSdkRunResult(used_sdk=True, error=f"project_settings_scrub_failed: {exc}")

    hooks = None
    if HookMatcher is not None:
        hooks = {
            "PreToolUse": [
                HookMatcher(
                    matcher=None if sandbox_brokered else "Bash",
                    hooks=[enforce_side_effect_tool_policy],
                )
            ],
        }
        if configured_skills:
            skill_hook = HookMatcher(matcher="Skill", hooks=[record_skill_tool_use])
            hooks["PostToolUse"] = [skill_hook]
            hooks["PostToolUseFailure"] = [skill_hook]

    options = ClaudeAgentOptions(
        cwd=str(cwd),
        model=model_id or settings.claude_agent_model or settings.anthropic_model or None,
        tools=(
            [*_SDK_LOCAL_READ_ONLY_TOOLS, *_SDK_BROKERED_BUILTIN_TOOLS]
            if sandbox_brokered
            else _sdk_tools_for_mode(full_access=full_access)
        ),
        mcp_servers={"ai-platform-context": context_retrieval_server} if context_retrieval_server is not None else {},
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        env=build_sdk_env(cwd=cwd),
        skills=configured_skills,
        session_id=session_id,
        max_turns=max(1, int(getattr(settings, "claude_agent_sdk_max_turns", 128))),
        max_thinking_tokens=max(1, int(getattr(settings, "claude_agent_sdk_max_thinking_tokens", 16384))),
        effort=str(getattr(settings, "claude_agent_sdk_effort", "xhigh") or "xhigh"),
        can_use_tool=can_use_tool,
        hooks=hooks,
        setting_sources=["project"],
    )

    texts: list[str] = []
    result_session_id: str | None = None
    usage: dict[str, Any] = {}

    async def consume() -> ClaudeAgentSdkRunResult:
        nonlocal result_session_id, usage
        async for message in query(prompt=_sdk_user_prompt_stream(prompt, session_id=session_id), options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                        if on_text and block.text:
                            await on_text(block.text)
            elif isinstance(message, ResultMessage):
                result_session_id = message.session_id
                usage = message.usage or message.model_usage or {}
                _append_result_text(texts, message.result)
                if message.is_error:
                    return ClaudeAgentSdkRunResult(
                        used_sdk=True,
                        message="\n".join(texts).strip(),
                        session_id=result_session_id,
                        usage=usage,
                        error="; ".join(message.errors or []) or message.stop_reason or "claude_agent_sdk_error",
                        used_skills=list(used_skill_names),
                        used_skills_source="executor_hook" if used_skill_names else "",
                    )
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            message="\n".join(texts).strip(),
            session_id=result_session_id,
            usage=usage,
            used_skills=list(used_skill_names),
            used_skills_source="executor_hook" if used_skill_names else "",
        )

    try:
        return await asyncio.wait_for(consume(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            message="\n".join(texts).strip(),
            session_id=result_session_id,
            usage=usage,
            error="claude_agent_sdk_timeout",
            used_skills=list(used_skill_names),
            used_skills_source="executor_hook" if used_skill_names else "",
        )
    except Exception as exc:
        return ClaudeAgentSdkRunResult(
            used_sdk=True,
            message="\n".join(texts).strip(),
            session_id=result_session_id,
            usage=usage,
            error=str(exc),
            used_skills=list(used_skill_names),
            used_skills_source="executor_hook" if used_skill_names else "",
        )
