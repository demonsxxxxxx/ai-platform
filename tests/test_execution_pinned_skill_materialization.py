import pytest

from app.artifacts import api as artifacts_api
from app.execution import api as execution_api
from app.skills import api as skills_api
from app.execution.api import select_execution_skill_names


def test_retained_execution_imports_delegate_to_owners():
    assert execution_api.build_artifact_records is artifacts_api.build_artifact_records
    assert execution_api.promote_artifact_reservations is artifacts_api.promote_artifact_reservations
    assert execution_api.PinnedSkillMismatch is skills_api.PinnedSkillMismatch
    assert execution_api.validate_pinned_skill_relative_path is skills_api.validate_pinned_skill_relative_path
    assert {"build_artifact_records", "promote_artifact_reservations"} <= set(execution_api.__all__)


@pytest.mark.parametrize(
    ("selected_skill_id", "available_skill_ids"),
    [
        ("qa-file-reviewer", ["qa-file-reviewer", "minimax-docx"]),
        (
            "ctd-32s73-stability-template-fill",
            ["ctd-32s73-stability-template-fill", "reference-fact-extraction", "general-chat"],
        ),
    ],
)
def test_selected_skill_does_not_infer_dependencies(selected_skill_id, available_skill_ids):
    assert select_execution_skill_names(
        selected_skill_id=selected_skill_id,
        requested_skill_ids=[],
        available_skill_ids=available_skill_ids,
        pinned_manifests={},
    ) == [selected_skill_id]


def test_pinned_skill_dependency_graph_is_expanded_in_order():
    assert select_execution_skill_names(
        selected_skill_id="qa-file-reviewer",
        requested_skill_ids=[],
        available_skill_ids=["qa-file-reviewer", "minimax-docx"],
        pinned_manifests={
            "qa-file-reviewer": {"dependency_ids": ["minimax-docx"]},
            "minimax-docx": {},
        },
    ) == ["qa-file-reviewer", "minimax-docx"]


def test_requested_skill_ids_keep_order_and_deduplicate_dependency_cycles():
    assert select_execution_skill_names(
        selected_skill_id="primary",
        requested_skill_ids=["other", "primary", "unavailable"],
        available_skill_ids=["primary", "other", "dependency"],
        pinned_manifests={
            "primary": {"dependency_ids": ["dependency"]},
            "dependency": {"dependency_ids": ["primary"]},
            "other": {},
        },
    ) == ["primary", "dependency", "other"]


def test_unavailable_selected_skill_does_not_stage_discoverable_skills():
    assert select_execution_skill_names(
        selected_skill_id="general-chat",
        requested_skill_ids=[],
        available_skill_ids=["qa-file-reviewer", "minimax-docx"],
        pinned_manifests={},
    ) == []


def test_authorized_catalog_keeps_its_order_without_request_expansion():
    assert select_execution_skill_names(
        selected_skill_id="qa-file-reviewer",
        requested_skill_ids=["unrelated"],
        available_skill_ids=["qa-file-reviewer", "minimax-docx", "unrelated"],
        pinned_manifests={"qa-file-reviewer": {"dependency_ids": ["unrelated"]}},
        authorized_skill_ids=("qa-file-reviewer", "minimax-docx"),
    ) == ["qa-file-reviewer", "minimax-docx"]
