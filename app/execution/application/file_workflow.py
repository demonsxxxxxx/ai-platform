from __future__ import annotations

from typing import Any, Protocol


class RunPayload(Protocol):
    input: dict[str, Any]
    skill_id: str | None
    skill_manifests: list[dict[str, Any]]
    run_id: str
    file_ids: list[str]


class ExecutorResult(Protocol):
    artifacts: list[Any]


class ArtifactLineageContract(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        source_run_id: object = None,
    ) -> dict[str, Any]: ...


class AuthorizedSkillCatalog(Protocol):
    materialized_skill_ids: list[str]


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def pinned_skill_manifests(payload: RunPayload) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("skill_id")).strip(): item
        for item in payload.skill_manifests
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    }


def with_pinned_manifest_dependencies(
    selected: list[str], pins: dict[str, dict[str, Any]]
) -> list[str]:
    expanded: list[str] = []

    def add_skill(skill_name: str) -> None:
        if skill_name in expanded:
            return
        expanded.append(skill_name)
        manifest = pins.get(skill_name)
        if not manifest:
            return
        for dependency_id in string_list(manifest.get("dependency_ids")):
            add_skill(dependency_id)

    for skill_name in selected:
        add_skill(skill_name)
    return expanded


def allowed_skill_names(
    payload: RunPayload,
    available_names: list[str],
    *,
    authorized_catalog: AuthorizedSkillCatalog | None = None,
) -> list[str]:
    available = set(available_names)
    if authorized_catalog is not None:
        return [
            skill_id
            for skill_id in authorized_catalog.materialized_skill_ids
            if skill_id in available
        ]
    requested = string_list(payload.input.get("skill_ids"))
    if payload.skill_id and payload.skill_id in available:
        requested.insert(0, payload.skill_id)
    if not requested:
        return []
    selected = list(dict.fromkeys(name for name in requested if name in available))
    pins = pinned_skill_manifests(payload)
    if pins:
        return with_pinned_manifest_dependencies(selected, pins)
    return selected


def file_skill_steps(input_payload: dict[str, object]) -> list[dict[str, object]]:
    raw_steps = input_payload.get("multi_agent_steps")
    if isinstance(raw_steps, list) and raw_steps:
        steps = []
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                continue
            role = str(
                raw_step.get("role") or raw_step.get("agent_role") or f"step-{index}"
            )
            step_key = str(
                raw_step.get("step_key")
                or raw_step.get("id")
                or role
                or f"step-{index}"
            )
            depends_on = (
                raw_step.get("depends_on")
                if isinstance(raw_step.get("depends_on"), list)
                else []
            )
            steps.append(
                {
                    "step_key": step_key,
                    "role": role,
                    "depends_on": [str(item) for item in depends_on],
                    "skill_ids": string_list(raw_step.get("skill_ids")),
                    "mcp_tool_ids": string_list(raw_step.get("mcp_tool_ids")),
                }
            )
        if steps:
            return steps
    return [
        {
            "step_key": "inspect",
            "role": "inspect",
            "depends_on": [],
            "skill_ids": [],
            "mcp_tool_ids": [],
        },
        {
            "step_key": "execute",
            "role": "execute",
            "depends_on": ["inspect"],
            "skill_ids": string_list(input_payload.get("skill_ids")),
            "mcp_tool_ids": string_list(input_payload.get("mcp_tool_ids")),
        },
        {
            "step_key": "verify",
            "role": "verify",
            "depends_on": ["execute"],
            "skill_ids": [],
            "mcp_tool_ids": [],
        },
    ]


def resume_completed_step_outputs(
    input_payload: dict[str, object],
) -> dict[str, str]:
    resume = input_payload.get("resume")
    if not isinstance(resume, dict):
        return {}
    outputs = resume.get("completed_step_outputs")
    if not isinstance(outputs, dict):
        return {}
    return {str(key): str(value) for key, value in outputs.items() if value is not None}


def resume_completed_step_checkpoints(
    input_payload: dict[str, object],
) -> dict[str, dict[str, object]]:
    resume = input_payload.get("resume")
    if not isinstance(resume, dict):
        return {}
    checkpoints = resume.get("completed_step_checkpoints")
    if not isinstance(checkpoints, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in checkpoints.items()
        if isinstance(value, dict)
    }


def resume_checkpoint_lineage(
    completed_checkpoints: dict[str, dict[str, object]],
    *,
    step_key: str,
    copied_from_run_id: object,
    lineage_contract: ArtifactLineageContract,
) -> dict[str, str]:
    checkpoint = completed_checkpoints.get(step_key)
    if not isinstance(checkpoint, dict):
        return {}
    lineage = lineage_contract(
        {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "source_step_id": checkpoint.get("source_step_id"),
        },
        source_run_id=checkpoint.get("copied_from_run_id") or copied_from_run_id,
    )
    checkpoint_id = lineage.get("checkpoint_id")
    source_step_id = lineage.get("source_step_id")
    source_run_id = lineage.get("source_run_id")
    if not checkpoint_id or not source_step_id:
        return {}
    result = {
        "checkpoint_id": str(checkpoint_id),
        "source_step_id": str(source_step_id),
    }
    if source_run_id:
        result["copied_from_run_id"] = str(source_run_id)
    return result


def completed_step_checkpoint_payload(
    payload: RunPayload,
    *,
    step_index: int,
    lineage_contract: ArtifactLineageContract,
) -> dict[str, str]:
    checkpoint_id = lineage_contract(
        {"checkpoint_id": f"checkpoint-{payload.run_id}-step-{step_index}"}
    ).get("checkpoint_id")
    if not checkpoint_id:
        return {}
    return {"checkpoint_id": str(checkpoint_id)}


def resume_copied_from_run_id(input_payload: dict[str, object]) -> str | None:
    resume = input_payload.get("resume")
    if not isinstance(resume, dict):
        return None
    copied_from_run_id = resume.get("copied_from_run_id")
    return str(copied_from_run_id) if copied_from_run_id else None


def is_file_skill_execution_step(step: dict[str, object]) -> bool:
    value = f"{step.get('step_key', '')} {step.get('role', '')}".lower()
    return any(token in value for token in ("review", "translate", "answer", "execute"))


def non_execution_step_output(
    *,
    step: dict[str, object],
    payload: RunPayload,
    skill_result: ExecutorResult | None,
) -> str:
    step_key = str(step.get("step_key") or "")
    role = str(step.get("role") or step_key or "agent")
    if step_key == "inspect" or role == "inspect":
        return f"Input inspected: {len(payload.file_ids)} file(s)."
    if step_key == "verify" or role == "verify":
        artifact_count = len(skill_result.artifacts) if skill_result else 0
        return f"Verification completed: {artifact_count} artifact(s) prepared."
    return f"{role} step completed."
