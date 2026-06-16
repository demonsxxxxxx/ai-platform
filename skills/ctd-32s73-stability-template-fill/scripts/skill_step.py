from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_guard import make_internal_env
from workflow_states import (
    ARTIFACT_BODY_SKELETON_DOCX,
    ARTIFACT_FACT_PACKET,
    RECOVERY_NONE,
    STATE_BODY_SKELETON_REQUIRED,
    STATE_BODY_SECTIONS_REQUIRED,
    STATE_BODY_SECTIONS_REVISION_REQUIRED,
    STATE_COMPLETED_FINAL,
    STATE_COMPLETED_INTERMEDIATE,
    STATE_FACT_EXTRACTION_REQUIRED,
    STATE_FACT_PROJECT_PROFILE_REQUIRED,
    STATE_FACT_PACKET_REVISION_REQUIRED,
    STATE_FACT_STUDY_SHARDS_REQUIRED,
    STATE_FAILED,
    STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED,
    STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED,
    STATE_TREND_CHARTS_REQUIRED,
    STATE_TREND_CHARTS_REVISION_REQUIRED,
    SUPPORTED_ARTIFACT_KEYS,
    accepted_input_artifacts_for_state,
    body_skeleton_missing,
    completed_intermediate_recoverable,
    delivery_status_for_state as workflow_delivery_status_for_state,
    recovery_mode_for_state as workflow_recovery_mode_for_state,
    state_is_paused_or_terminal,
    step_done_for_state,
    terminal_kind_for_state as workflow_terminal_kind_for_state,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
STATE_MACHINE_SCRIPT = SKILL_ROOT / "scripts" / "run_state_machine_workflow.py"
STEP_EVENT_SCHEMA = "ctd-32s73-step-event-v1"
STEP_RESPONSE_SCHEMA = "ctd-32s73-step-response-v1"
STEP_CONFIG_FILENAME = "step-config.json"

FORBIDDEN_TOP_LEVEL_KEYS = {
    "action",
    "tool_call",
    "next_state",
    "state",
    "submit",
    "finalize",
    "render",
    "validate",
}
SUPPORTED_HOOK_KEYS = {
    "recovery_hook": "--recovery-hook",
    "body_skeleton_hook": "--body-skeleton-hook",
}
SUPPORTED_BOOLEAN_OPTIONS = {
    "allow_intermediate": "--allow-intermediate",
    "auto_ocr_pdf": "--auto-ocr-pdf",
    "disallow_hour_time": "--disallow-hour-time",
}
SUPPORTED_VALUE_OPTIONS = {
    "until": "--until",
    "artifact_profile": "--artifact-profile",
    "max_recovery_attempts": "--max-recovery-attempts",
    "min_tables": "--min-tables",
    "max_tables": "--max-tables",
}
SUPPORTED_LIST_OPTIONS = {
    "expected_batch": "--expected-batch",
    "expected_batches": "--expected-batch",
    "expected_warning": "--expected-warning",
    "expected_warnings": "--expected-warning",
}
SUPPORTED_OPTION_VALUES = {
    "artifact_profile": {"delivery", "audit", "full"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def append_event(output_dir: Path, event: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "workflow-events.jsonl"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": now_iso(), **event}, ensure_ascii=False) + "\n")


def is_relative_path(value: Any) -> bool:
    if value is None or value == "":
        return False
    return not user_path(value).is_absolute()


def user_path(value: Any) -> Path:
    text = str(value)
    path = Path(text)
    if text == "~" or text.startswith("~/") or text.startswith("~\\"):
        return path.expanduser()
    return path


def resolve_path(value: str | Path, base_dir: Path | None = None, field_name: str = "path") -> Path:
    path = user_path(value)
    if path.is_absolute():
        return path.resolve()
    if base_dir is None:
        raise ValueError(f"Relative {field_name} requires project_root: {value}")
    return (base_dir / path).resolve()


def project_root_from_event(event: dict[str, Any]) -> Path | None:
    value = event.get("project_root")
    if not value:
        return None
    return user_path(value).resolve()


def step_config_path(output_dir: Path) -> Path:
    return output_dir / STEP_CONFIG_FILENAME


def load_step_config(output_dir: Path) -> dict[str, Any]:
    config = read_json_if_exists(step_config_path(output_dir))
    return config if isinstance(config, dict) else {}


def update_step_config(output_dir: Path, event: dict[str, Any], project_root: Path | None) -> dict[str, Any]:
    config = load_step_config(output_dir)
    if project_root is not None:
        config["project_root"] = str(project_root)
    base_dir = project_root or (Path(config["project_root"]) if config.get("project_root") else None)

    hooks = event.get("hooks")
    if isinstance(hooks, dict) and hooks:
        existing_hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        resolved_hooks = dict(existing_hooks)
        for key, value in hooks.items():
            if value:
                resolved_hooks[key] = str(resolve_path(str(value), base_dir, f"hooks.{key}"))
        config["hooks"] = resolved_hooks

    options = event.get("options")
    if isinstance(options, dict) and options:
        existing_options = config.get("options") if isinstance(config.get("options"), dict) else {}
        merged_options = dict(existing_options)
        merged_options.update(options)
        config["options"] = merged_options

    if config:
        write_json(step_config_path(output_dir), config)
    return config


def effective_event(event: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(event)
    if "project_root" not in merged and config.get("project_root"):
        merged["project_root"] = config["project_root"]
    if isinstance(config.get("hooks"), dict):
        hooks = dict(config["hooks"])
        hooks.update(event.get("hooks") or {})
        merged["hooks"] = hooks
    if isinstance(config.get("options"), dict):
        options = dict(config["options"])
        options.update(event.get("options") or {})
        merged["options"] = options
    return merged


def source_path(source: Any) -> str:
    if isinstance(source, dict):
        return str(source.get("path") or source.get("file") or "")
    return str(source)


def state_sources(state: dict[str, Any] | None, key: str) -> list[str]:
    if not state:
        return []
    sources = state.get("sources")
    if not isinstance(sources, dict):
        return []
    values = sources.get(key)
    if not isinstance(values, list):
        return []
    return [path for item in values if (path := source_path(item))]


def event_sources(event: dict[str, Any], key: str, state: dict[str, Any] | None, base_dir: Path | None) -> list[str]:
    values = event.get(key)
    if isinstance(values, list) and values:
        paths = []
        for idx, item in enumerate(values):
            if path := source_path(item):
                paths.append(str(resolve_path(path, base_dir, f"{key}[{idx}]")))
        return paths
    return state_sources(state, "allowed" if key == "sources" else "forbidden")


def load_existing_state(output_dir: Path) -> dict[str, Any] | None:
    state_path = output_dir / "workflow-state.json"
    if not state_path.exists():
        return None
    return read_json(state_path)


def current_state_name(state: dict[str, Any] | None) -> str | None:
    if not state:
        return None
    value = state.get("state")
    return str(value) if value else None


def validate_event_shape(event: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(event, dict):
        return ["Step event must be a JSON object."]
    if event.get("schema_version") != STEP_EVENT_SCHEMA:
        problems.append(f"schema_version must be {STEP_EVENT_SCHEMA!r}.")
    if "output_dir" not in event:
        problems.append("output_dir is required.")
    project_root_value = event.get("project_root")
    if project_root_value and is_relative_path(project_root_value):
        problems.append("project_root must be an absolute path when provided.")
    relative_fields: list[str] = []
    if "output_dir" in event and is_relative_path(event.get("output_dir")):
        relative_fields.append("output_dir")
    for key in ["sources", "forbidden_sources"]:
        values = event.get(key)
        if isinstance(values, list):
            for idx, item in enumerate(values):
                if is_relative_path(source_path(item)):
                    relative_fields.append(f"{key}[{idx}]")
    provided = event.get("provided_artifacts", {})
    if isinstance(provided, dict):
        for key, value in provided.items():
            if value and is_relative_path(value):
                relative_fields.append(f"provided_artifacts.{key}")
    hooks = event.get("hooks", {})
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        problems.append("hooks must be an object when present.")
    else:
        unsupported_hooks = sorted(set(hooks) - set(SUPPORTED_HOOK_KEYS))
        if unsupported_hooks:
            problems.append(f"Unsupported hook keys: {unsupported_hooks}.")
        for key, value in hooks.items():
            if value and is_relative_path(value):
                relative_fields.append(f"hooks.{key}")
    options = event.get("options", {})
    if options is None:
        options = {}
    if not isinstance(options, dict):
        problems.append("options must be an object when present.")
    else:
        supported_options = set(SUPPORTED_BOOLEAN_OPTIONS) | set(SUPPORTED_VALUE_OPTIONS) | set(SUPPORTED_LIST_OPTIONS)
        unsupported_options = sorted(set(options) - supported_options)
        if unsupported_options:
            problems.append(f"Unsupported option keys: {unsupported_options}.")
        for key, allowed_values in SUPPORTED_OPTION_VALUES.items():
            if options.get(key) is not None and str(options[key]) not in allowed_values:
                problems.append(f"Unsupported {key}: {options[key]!r}; expected one of {sorted(allowed_values)}.")
    if relative_fields and not project_root_value:
        problems.append(f"Relative paths require project_root: {relative_fields}.")
    forbidden = sorted(key for key in FORBIDDEN_TOP_LEVEL_KEYS if key in event)
    if forbidden:
        problems.append(f"Step events may not request actions or state jumps: {forbidden}.")
    if provided is None:
        provided = {}
    if not isinstance(provided, dict):
        problems.append("provided_artifacts must be an object when present.")
    return problems


def provided_artifacts(event: dict[str, Any], base_dir: Path | None) -> dict[str, Path]:
    raw = event.get("provided_artifacts") or {}
    artifacts: dict[str, Path] = {}
    for key, value in raw.items():
        if value:
            artifacts[str(key)] = resolve_path(str(value), base_dir, f"provided_artifacts.{key}")
    return artifacts


def is_recoverable_fact_packet_failure(state: dict[str, Any] | None) -> bool:
    if not state or state.get("state") != "FAILED":
        return False
    if str(state.get("previous_states", [])[-1].get("reason", "") if state.get("previous_states") else "") == "fact packet validation failed":
        return True
    commands = state.get("commands")
    if isinstance(commands, dict):
        validation = commands.get("validate_fact_packet")
        if isinstance(validation, dict) and validation.get("returncode") not in {None, 0}:
            return True
    return False


def recovery_mode_for_state(state: dict[str, Any] | None) -> str:
    state_name = current_state_name(state)
    blocking_reasons = state.get("blocking_reasons") if state else []
    if not isinstance(blocking_reasons, list):
        blocking_reasons = []
    return workflow_recovery_mode_for_state(
        state_name,
        blocking_reasons,
        recoverable_fact_packet_failure=is_recoverable_fact_packet_failure(state),
    )


def terminal_kind_for_state(state_name: str | None, final: bool, required_artifacts: list[dict[str, str]]) -> str:
    return workflow_terminal_kind_for_state(state_name, final, bool(required_artifacts))


def delivery_status_for_state(state_name: str | None, final: bool) -> str:
    return workflow_delivery_status_for_state(state_name, final)


LEGACY_FACT_SHARD_KEYS = {
    "project_profile_facts",
    "long_term_stability_facts",
    "accelerated_stability_facts",
    "stress_study_facts",
}


def artifact_acceptance_errors(artifacts: dict[str, Path], state: dict[str, Any] | None) -> list[str]:
    state_name = current_state_name(state)
    blocking_reasons = state.get("blocking_reasons") if state else []
    if not isinstance(blocking_reasons, list):
        blocking_reasons = []
    accepted_artifacts = accepted_input_artifacts_for_state(
        state_name,
        blocking_reasons,
        recoverable_fact_packet_failure=is_recoverable_fact_packet_failure(state),
    )
    errors: list[str] = []
    for key, path in artifacts.items():
        if key in LEGACY_FACT_SHARD_KEYS:
            errors.append("基础事实抽取已迁移到 reference-fact-extraction；本 skill 只接受 provided_artifacts.fact_packet，不再接收 fact shard artifact。")
            continue
        if key not in SUPPORTED_ARTIFACT_KEYS:
            errors.append(f"Unsupported provided artifact {key!r}; supported keys are {sorted(SUPPORTED_ARTIFACT_KEYS)}.")
            continue
        if not path.exists():
            errors.append(f"Provided artifact does not exist: {key}={path}.")
            continue
        if key not in accepted_artifacts:
            errors.append(f"{key} is not allowed while current state is {state_name!r}.")
    return errors


def append_hooks_and_options(command: list[str], event: dict[str, Any], base_dir: Path | None) -> None:
    hooks = event.get("hooks") or {}
    if isinstance(hooks, dict):
        for key, flag in SUPPORTED_HOOK_KEYS.items():
            value = hooks.get(key)
            if value:
                command.extend([flag, str(resolve_path(str(value), base_dir, f"hooks.{key}"))])

    options = event.get("options") or {}
    if not isinstance(options, dict):
        return
    for key, flag in SUPPORTED_BOOLEAN_OPTIONS.items():
        if bool(options.get(key)):
            command.append(flag)
    for key, flag in SUPPORTED_VALUE_OPTIONS.items():
        if options.get(key) is not None and options.get(key) != "":
            command.extend([flag, str(options[key])])
    for key, flag in SUPPORTED_LIST_OPTIONS.items():
        values = options.get(key)
        if values is None or values == "":
            continue
        if not isinstance(values, list):
            values = [values]
        for value in values:
            command.extend([flag, str(value)])


def artifact_profile_from_event(event: dict[str, Any]) -> str:
    options = event.get("options") if isinstance(event.get("options"), dict) else {}
    return str(options.get("artifact_profile") or "delivery")


def state_has_pdf_sources(state: dict[str, Any] | None) -> bool:
    for source in state_sources(state, "allowed"):
        if user_path(source).suffix.lower() == ".pdf":
            return True
    return False


def build_workflow_command(event: dict[str, Any], state: dict[str, Any] | None, artifacts: dict[str, Path], output_dir: Path, base_dir: Path | None) -> list[str]:
    command = [sys.executable, str(STATE_MACHINE_SCRIPT), "--output-dir", str(output_dir)]
    if state is not None:
        command.append("--resume")
    for source in event_sources(event, "sources", state, base_dir):
        command.extend(["--source", source])
    for source in event_sources(event, "forbidden_sources", state, base_dir):
        command.extend(["--forbidden-source", source])
    if "fact_packet" in artifacts:
        command.extend(["--fact-packet", str(artifacts["fact_packet"])])
    if "body_skeleton_docx" in artifacts:
        command.extend(["--working-docx", str(artifacts["body_skeleton_docx"])])
    append_hooks_and_options(command, event, base_dir)
    return command


def run_workflow(command: list[str], output_dir: Path, artifact_profile: str) -> dict[str, Any]:
    append_event(output_dir, {"event": "step_workflow_started", "command": command})
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=make_internal_env("skill_step.py", "advance CTD 3.2.S.7.3 workflow"),
    )
    output_base = output_dir if artifact_profile == "full" else output_dir / "_debug"
    output_base.mkdir(parents=True, exist_ok=True)
    stdout_path = output_base / "skill-step-workflow-output.txt"
    stderr_path = output_dir / "skill-step-workflow-output.txt.stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_value = None
    if completed.stderr:
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        stderr_value = str(stderr_path)
    append_event(output_dir, {"event": "step_workflow_finished", "returncode": completed.returncode})
    return {
        "returncode": completed.returncode,
        "stdout": str(stdout_path),
        "stderr": stderr_value,
    }


def required_artifacts_for_state(state_name: str | None, output_dir: Path, blocking_reasons: list[str]) -> list[dict[str, str]]:
    if state_name in {STATE_FACT_PROJECT_PROFILE_REQUIRED, STATE_FACT_STUDY_SHARDS_REQUIRED, STATE_FACT_EXTRACTION_REQUIRED, STATE_FACT_PACKET_REVISION_REQUIRED}:
        return [
            {
                "name": ARTIFACT_FACT_PACKET,
                "path": str(output_dir / "fact-packet.json"),
                "reason": "基础事实抽取已迁移到 reference-fact-extraction；请使用 ctd-32s73-stability profile 生成 CTD-native fact-packet.json 后作为 provided_artifacts.fact_packet 提交。",
            }
        ]
    if state_name == STATE_MISSING_EVIDENCE_RECOVERY_REQUIRED:
        return [
            {
                "name": ARTIFACT_FACT_PACKET,
                "path": str(output_dir / "fact-packet.json"),
                "reason": "事实包仍有阻塞性缺失；补查允许来源并更新 recovery attempts 后再提交。",
            }
        ]
    if state_name == STATE_TREND_CHARTS_REQUIRED:
        return [
            {
                "name": ARTIFACT_FACT_PACKET,
                "path": str(output_dir / "fact-packet.json"),
                "reason": "正文和表格事实包已通过基础校验；现在补充 trend_charts 后提交同一 fact-packet.json。",
            }
        ]
    if state_name == STATE_TREND_CHARTS_REVISION_REQUIRED:
        return [
            {
                "name": ARTIFACT_FACT_PACKET,
                "path": str(output_dir / "fact-packet.json"),
                "reason": "趋势图校验未通过；按 trend-charts-validation.json 修订 trend_charts 后提交同一 fact-packet.json。",
            }
        ]
    if state_name == STATE_BODY_SECTIONS_REQUIRED:
        return [
            {
                "name": ARTIFACT_FACT_PACKET,
                "path": str(output_dir / "fact-packet.json"),
                "reason": "趋势图数据已通过校验；现在按 body-sections-request.json 和 writing-patterns.md 补充 body_sections 后提交同一 fact-packet.json。",
            }
        ]
    if state_name == STATE_BODY_SECTIONS_REVISION_REQUIRED:
        return [
            {
                "name": ARTIFACT_FACT_PACKET,
                "path": str(output_dir / "fact-packet.json"),
                "reason": "正文校验未通过；按 body-sections-validation.json 修订 body_sections 后提交同一 fact-packet.json。",
            }
        ]
    if state_name == STATE_BODY_SKELETON_REQUIRED:
        return [
            {
                "name": ARTIFACT_BODY_SKELETON_DOCX,
                "path": str(output_dir / "body-and-skeleton-filled.docx"),
                "reason": "渲染计划需要项目专用正文/骨架 DOCX；表格数量由表块占位符和 table_render_inputs 自动生成。",
            }
        ]
    if state_name == STATE_COMPLETED_INTERMEDIATE and completed_intermediate_recoverable(blocking_reasons):
        reason = "当前仅为中间产物；修正项目专用正文/骨架或补充验证 options 后，在同一 output_dir 重新提交。"
        if body_skeleton_missing(blocking_reasons):
            reason = "当前仅为中间产物；补充项目专用正文/骨架后，在同一 output_dir 继续。"
        return [
            {
                "name": ARTIFACT_BODY_SKELETON_DOCX,
                "path": str(output_dir / "body-and-skeleton-filled.docx"),
                "reason": reason,
            }
        ]
    return []


def allowed_next_events(required_artifacts: list[dict[str, str]], output_dir: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    if not required_artifacts:
        return []
    event: dict[str, Any] = {
        "schema_version": STEP_EVENT_SCHEMA,
        "output_dir": str(output_dir),
        "provided_artifacts": {artifact["name"]: artifact["path"] for artifact in required_artifacts},
    }
    if config.get("project_root"):
        event["project_root"] = config["project_root"]
    if isinstance(config.get("hooks"), dict) and config["hooks"]:
        event["hooks"] = config["hooks"]
    if isinstance(config.get("options"), dict) and config["options"]:
        event["options"] = config["options"]
    return [event]


def response_reply(state_name: str | None, final: bool, required_artifacts: list[dict[str, str]], blocking_reasons: list[str]) -> str:
    if final and state_name == STATE_COMPLETED_FINAL:
        return "状态机已达到 COMPLETED_FINAL，验证通过，可交付最终候选。"
    if required_artifacts:
        names = "、".join(artifact["name"] for artifact in required_artifacts)
        return f"状态机停在 {state_name}，下一步只能提交：{names}。"
    if state_name == STATE_COMPLETED_INTERMEDIATE:
        return "状态机只完成中间产物，不能作为最终申报章节交付。"
    if state_name == STATE_FACT_PACKET_REVISION_REQUIRED:
        return "状态机停在 FACT_PACKET_REVISION_REQUIRED；请修订并重新提交 reference-fact-extraction 生成的 CTD-native fact_packet。"
    if state_name in {STATE_FACT_PROJECT_PROFILE_REQUIRED, STATE_FACT_STUDY_SHARDS_REQUIRED, STATE_FACT_EXTRACTION_REQUIRED}:
        return "状态机停在基础事实输入阶段；本 skill 不再接收 fact shard，请先运行 reference-fact-extraction 的 ctd-32s73-stability profile 并提交 fact_packet。"
    if state_name == STATE_TREND_CHARTS_REQUIRED:
        return "状态机停在 TREND_CHARTS_REQUIRED；正文和表格事实已通过校验，下一步只能提交补充 trend_charts 的 fact_packet。"
    if state_name == STATE_TREND_CHARTS_REVISION_REQUIRED:
        return "状态机停在 TREND_CHARTS_REVISION_REQUIRED；按 trend-charts-validation.json 修订趋势图数据后只能提交：fact_packet。"
    if state_name == STATE_BODY_SECTIONS_REQUIRED:
        return "状态机停在 BODY_SECTIONS_REQUIRED；趋势图已通过校验，下一步只能提交补充 body_sections 的 fact_packet。"
    if state_name == STATE_BODY_SECTIONS_REVISION_REQUIRED:
        return "状态机停在 BODY_SECTIONS_REVISION_REQUIRED；按 body-sections-validation.json 修订正文后只能提交：fact_packet。"
    if state_name in {STATE_FAILED, STATE_MISSING_EVIDENCE_RECOVERY_EXHAUSTED}:
        return f"状态机停在 {state_name}，需要先处理阻塞原因。"
    if blocking_reasons:
        return f"状态机停在 {state_name}，存在阻塞原因。"
    return f"状态机当前状态：{state_name or 'NEW'}。"


def build_response(output_dir: Path, workflow_result: dict[str, Any] | None = None, rejection: list[str] | None = None) -> dict[str, Any]:
    state = load_existing_state(output_dir)
    state_name = current_state_name(state)
    summary_path = output_dir / "workflow-summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    config = load_step_config(output_dir)
    blocking_reasons = list(summary.get("blocking_reasons") or (state or {}).get("blocking_reasons") or [])
    final = bool(summary.get("final") or (state or {}).get("final"))
    required_artifacts = required_artifacts_for_state(state_name, output_dir, blocking_reasons)
    response = {
        "schema_version": STEP_RESPONSE_SCHEMA,
        "state": state_name,
        "reply": response_reply(state_name, final, required_artifacts, blocking_reasons),
        "done": step_done_for_state(state_name, final, bool(required_artifacts)),
        "final": final,
        "terminal_kind": terminal_kind_for_state(state_name, final, required_artifacts),
        "delivery_status": delivery_status_for_state(state_name, final),
        "recoverable": bool(required_artifacts or recovery_mode_for_state(state) != RECOVERY_NONE),
        "recovery_mode": recovery_mode_for_state(state),
        "allowed_next_events": allowed_next_events(required_artifacts, output_dir, config),
        "required_artifacts": required_artifacts,
        "blocking_reasons": blocking_reasons,
        "summary_path": str(summary_path) if summary_path.exists() else None,
    }
    artifacts = (state or {}).get("artifacts")
    if isinstance(artifacts, dict):
        response["artifacts"] = artifacts
    if state and state.get("artifact_profile"):
        response["artifact_profile"] = state.get("artifact_profile")
    if state and state.get("artifact_directories"):
        response["artifact_directories"] = state.get("artifact_directories")
    if workflow_result is not None:
        response["workflow_result"] = workflow_result
    if rejection:
        response["status"] = "rejected"
        response["rejection_reasons"] = rejection
        response["reply"] = "Step event 被拒绝；状态机不会消费当前输入。"
    return response


def should_run_workflow(state_name: str | None, state: dict[str, Any] | None, artifacts: dict[str, Path], event: dict[str, Any]) -> bool:
    if state_name is None:
        return True
    if artifacts:
        return True
    if state_name in {STATE_FACT_EXTRACTION_REQUIRED, STATE_FACT_PROJECT_PROFILE_REQUIRED, STATE_FACT_STUDY_SHARDS_REQUIRED} and state_has_pdf_sources(state):
        return True
    return not state_is_paused_or_terminal(state_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Public step entrypoint for the CTD 3.2.S.7.3 stability skill FSM.")
    parser.add_argument("--event", type=Path, required=True, help="Path to a ctd-32s73-step-event-v1 JSON file.")
    parser.add_argument("--response-out", type=Path, help="Optional path for the ctd-32s73-step-response-v1 JSON response.")
    args = parser.parse_args()

    event_path = args.event.resolve()
    raw_event = read_json(event_path)
    shape_errors = validate_event_shape(raw_event)
    event = raw_event if isinstance(raw_event, dict) else {}
    project_root = project_root_from_event(event)
    try:
        output_dir = resolve_path(event.get("output_dir", "outputs/rejected-step-events"), project_root, "output_dir")
    except ValueError:
        output_dir = SKILL_ROOT / "outputs" / "rejected-step-events"
    response_out = args.response_out.resolve() if args.response_out else output_dir / "step-response.json"

    if shape_errors:
        append_event(output_dir, {"event": "step_event_rejected", "reasons": shape_errors})
        response = build_response(output_dir, rejection=shape_errors)
        write_json(response_out, response)
        print(json.dumps(response, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    config = update_step_config(output_dir, event, project_root)
    event = effective_event(event, config)
    project_root = project_root_from_event(event)
    state = load_existing_state(output_dir)
    state_name = current_state_name(state)
    artifacts = provided_artifacts(event, project_root)
    artifact_errors = artifact_acceptance_errors(artifacts, state)
    append_event(
        output_dir,
        {
            "event": "step_event_received",
            "step_event": str(event_path),
            "state": state_name,
            "provided_artifacts": {key: str(value) for key, value in artifacts.items()},
        },
    )
    if artifact_errors:
        append_event(output_dir, {"event": "step_event_rejected", "state": state_name, "reasons": artifact_errors})
        response = build_response(output_dir, rejection=artifact_errors)
        write_json(response_out, response)
        print(json.dumps(response, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    workflow_result = None
    if should_run_workflow(state_name, state, artifacts, event):
        command = build_workflow_command(event, state, artifacts, output_dir, project_root)
        workflow_result = run_workflow(command, output_dir, artifact_profile_from_event(event))

    response = build_response(output_dir, workflow_result=workflow_result)
    write_json(response_out, response)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if workflow_result and workflow_result["returncode"] != 0:
        raise SystemExit(workflow_result["returncode"])


if __name__ == "__main__":
    main()
