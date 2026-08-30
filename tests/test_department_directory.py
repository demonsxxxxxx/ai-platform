from __future__ import annotations

import importlib

import httpx
import pytest
from pydantic import ValidationError

from app.department_directory import (
    DepartmentDirectoryError,
    fetch_department_directory,
    normalize_department_directory,
    validate_distribution_department_authorities,
)
from app.models import CapabilityDistributionAuthorityUpdateRequest


def test_projects_pure_tree_and_disables_normalized_label_collisions():
    directory = normalize_department_directory(
        [
            {
                "value": "1",
                "parentId": "1",
                "label": "总部",
                "children": [
                    {"value": "2", "parentId": "1", "label": "Research", "children": []},
                ],
            },
            {
                "value": "3",
                "parentId": "1",
                "label": "分部",
                "children": [
                    {"value": "4", "parentId": "3", "label": "ＲＥＳＥＡＲＣＨ", "children": []},
                ],
            },
        ]
    )

    assert [node.path for node in directory.departments] == ["总部", "分部"]
    duplicates = [directory.departments[0].children[0], directory.departments[1].children[0]]
    assert [(node.selectable, node.reason) for node in duplicates] == [
        (False, "duplicate_authority_id"),
        (False, "duplicate_authority_id"),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a tree"},
        [{"value": "employee:1", "parentId": "1", "label": "employee", "children": []}],
        [{"value": "2", "parentId": "1", "label": "QA", "children": [], "mobile": "secret"}],
        [{"value": "2", "parentId": "0", "label": "QA", "children": []}],
        [{"value": "2", "parentId": "1", "label": "QA\u0000", "children": []}],
        [{"value": "2", "parentId": "1", "label": "QA\n", "children": []}],
        [{"value": "2", "parentId": "1", "label": "\tQA", "children": []}],
        [{"value": "2", "parentId": "1", "label": "\u0085QA", "children": []}],
        [{"value": "2", "parentId": "1", "label": "QA,RD", "children": []}],
        [{"value": "2", "parentId": "1", "label": "部" * 161, "children": []}],
        [{"value": " 2", "parentId": "1", "label": "QA", "children": []}],
        [
            {"value": "2", "parentId": "1", "label": "QA", "children": []},
            {"value": "2", "parentId": "1", "label": "RD", "children": []},
        ],
    ],
    ids=[
        "not-list",
        "employee-id",
        "employee-metadata",
        "wrong-root",
        "nul-label",
        "newline-label",
        "tab-label",
        "nel-label",
        "comma-label",
        "overlong-label",
        "padded-id",
        "duplicate-id",
    ],
)
def test_rejects_non_pure_or_untrusted_nodes(payload):
    with pytest.raises(DepartmentDirectoryError, match="department_directory_shape_invalid"):
        normalize_department_directory(payload)


def test_fails_closed_at_node_and_depth_bounds():
    too_many = [
        {"value": str(index + 1), "parentId": "1", "label": f"dept-{index}", "children": []}
        for index in range(5_001)
    ]
    root = {"value": "1", "parentId": "1", "label": "root", "children": []}
    current = root
    for depth in range(2, 14):
        child = {
            "value": str(depth),
            "parentId": str(depth - 1),
            "label": f"depth-{depth}",
            "children": [],
        }
        current["children"] = [child]
        current = child

    with pytest.raises(DepartmentDirectoryError, match="department_directory_shape_invalid"):
        normalize_department_directory(too_many)
    with pytest.raises(DepartmentDirectoryError, match="department_directory_shape_invalid"):
        normalize_department_directory([root])


def test_authority_selection_accepts_only_exact_selectable_directory_values():
    directory = normalize_department_directory(
        [
            {"value": "1", "parentId": "1", "label": "QA", "children": []},
            {"value": "2", "parentId": "1", "label": "Research", "children": []},
            {"value": "3", "parentId": "1", "label": "ＲＥＳＥＡＲＣＨ", "children": []},
        ]
    )

    assert validate_distribution_department_authorities(["QA", "QA"], directory) == ["QA"]
    for invalid in (["UNKNOWN"], ["Research"], ["\u0000"], [" QA"], ["QA\n"]):
        with pytest.raises(
            DepartmentDirectoryError,
            match="capability_distribution_department_authority_invalid",
        ):
            validate_distribution_department_authorities(invalid, directory)


def test_authority_update_request_preserves_labels_for_route_proof_and_bounds_count():
    request = CapabilityDistributionAuthorityUpdateRequest(
        department_ids=["药品注册", " QA "],
        allowed_roles=[" QA_REVIEWER ", "qa_reviewer"],
    )

    assert request.department_ids == ["药品注册", " QA "]
    assert request.allowed_roles == ["qa_reviewer"]
    with pytest.raises(ValidationError):
        CapabilityDistributionAuthorityUpdateRequest(
            department_ids=[f"department-{index}" for index in range(129)]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("timeout", "department_directory_timeout"),
        ("non-success", "department_directory_upstream_unavailable"),
        ("bad-json", "department_directory_upstream_unavailable"),
    ],
)
async def test_adapter_maps_transport_failures_to_stable_codes(monkeypatch, mode, expected_code):
    client_options = {}
    requested_urls = []

    class FakeResponse:
        status_code = 502 if mode == "non-success" else 200

        def json(self):
            if mode == "bad-json":
                raise ValueError("private response")
            return []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            requested_urls.append(url)
            if mode == "timeout":
                raise httpx.ReadTimeout("private timeout")
            return FakeResponse()

    directory_module = importlib.import_module("app.department_directory")
    monkeypatch.setattr(
        directory_module.httpx,
        "AsyncClient",
        lambda **kwargs: (client_options.update(kwargs), FakeClient())[1],
    )

    with pytest.raises(DepartmentDirectoryError, match=expected_code):
        await fetch_department_directory()

    assert requested_urls == ["http://10.56.0.25:5033/api/DingTalk/departs/pure"]
    assert client_options == {"timeout": 5.0, "follow_redirects": False, "trust_env": False}
