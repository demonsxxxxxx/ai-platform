"""Fail-closed projection of the company pure-department directory."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import unicodedata

import httpx

from app.models import DepartmentDirectoryNodeResponse, DepartmentDirectoryResponse
from app.validation import assert_safe_department_authority_id

PURE_DEPARTMENT_DIRECTORY_URL = "http://10.56.0.25:5033/api/DingTalk/departs/pure"
PURE_DEPARTMENT_DIRECTORY_TIMEOUT_SECONDS = 5.0
MAX_DIRECTORY_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DIRECTORY_NODES = 5_000
MAX_DIRECTORY_DEPTH = 12
MAX_DISTRIBUTION_DEPARTMENTS = 128
ROOT_PARENT_ID = "1"
_PURE_NODE_KEYS = frozenset({"value", "parentId", "label", "children"})


class DepartmentDirectoryError(RuntimeError):
    """Stable public error code for directory failures."""


@dataclass(slots=True)
class _ParsedNode:
    directory_id: str
    label: str
    children: list["_ParsedNode"] = field(default_factory=list)


def _safe_numeric_id(value: object) -> str:
    if not isinstance(value, str):
        raise DepartmentDirectoryError("department_directory_shape_invalid")
    candidate = value
    if not candidate or not candidate.isascii() or not candidate.isdecimal():
        raise DepartmentDirectoryError("department_directory_shape_invalid")
    return candidate


def _safe_label(value: object) -> str:
    if not isinstance(value, str):
        raise DepartmentDirectoryError("department_directory_shape_invalid")
    try:
        return assert_safe_department_authority_id(value, "department")
    except ValueError as exc:
        raise DepartmentDirectoryError("department_directory_shape_invalid") from exc


def _authority_key(label: str) -> str:
    return unicodedata.normalize("NFKC", label).casefold()


def normalize_department_directory(payload: object) -> DepartmentDirectoryResponse:
    """Accept only a pure tree and disable every ambiguous authority label."""

    if not isinstance(payload, list):
        raise DepartmentDirectoryError("department_directory_shape_invalid")

    seen_ids: set[str] = set()
    parsed_nodes: list[_ParsedNode] = []
    node_count = 0

    def parse(raw: object, *, expected_parent_id: str | None, depth: int) -> _ParsedNode:
        nonlocal node_count
        if depth > MAX_DIRECTORY_DEPTH or not isinstance(raw, dict) or frozenset(raw) != _PURE_NODE_KEYS:
            raise DepartmentDirectoryError("department_directory_shape_invalid")
        node_count += 1
        if node_count > MAX_DIRECTORY_NODES:
            raise DepartmentDirectoryError("department_directory_shape_invalid")

        directory_id = _safe_numeric_id(raw.get("value"))
        parent_id = _safe_numeric_id(raw.get("parentId"))
        label = _safe_label(raw.get("label"))
        children = raw.get("children")
        if directory_id in seen_ids or not isinstance(children, list):
            raise DepartmentDirectoryError("department_directory_shape_invalid")
        if expected_parent_id is None:
            if parent_id != ROOT_PARENT_ID:
                raise DepartmentDirectoryError("department_directory_shape_invalid")
        elif parent_id != expected_parent_id:
            raise DepartmentDirectoryError("department_directory_shape_invalid")

        seen_ids.add(directory_id)
        node = _ParsedNode(directory_id=directory_id, label=label)
        parsed_nodes.append(node)
        node.children = [
            parse(child, expected_parent_id=directory_id, depth=depth + 1)
            for child in children
        ]
        return node

    roots = [parse(item, expected_parent_id=None, depth=1) for item in payload]
    label_counts = Counter(_authority_key(node.label) for node in parsed_nodes)

    def project(node: _ParsedNode, parent_path: str) -> DepartmentDirectoryNodeResponse:
        path = f"{parent_path} / {node.label}" if parent_path else node.label
        duplicate = label_counts[_authority_key(node.label)] > 1
        return DepartmentDirectoryNodeResponse(
            directory_id=node.directory_id,
            authority_id=node.label,
            name=node.label,
            path=path,
            children=[project(child, path) for child in node.children],
            selectable=not duplicate,
            reason="duplicate_authority_id" if duplicate else None,
        )

    return DepartmentDirectoryResponse(departments=[project(root, "") for root in roots])


def validate_distribution_department_authorities(
    values: list[str],
    directory: DepartmentDirectoryResponse,
) -> list[str]:
    """Return exact selectable authority labels or reject the complete write."""

    if len(values) > MAX_DISTRIBUTION_DEPARTMENTS:
        raise DepartmentDirectoryError("capability_distribution_department_authority_invalid")
    selectable: set[str] = set()
    pending = list(directory.departments)
    while pending:
        node = pending.pop()
        pending.extend(node.children)
        if node.selectable:
            selectable.add(node.authority_id)

    normalized: list[str] = []
    for value in values:
        try:
            candidate = _safe_label(value)
        except DepartmentDirectoryError as exc:
            raise DepartmentDirectoryError("capability_distribution_department_authority_invalid") from exc
        if candidate not in selectable:
            raise DepartmentDirectoryError("capability_distribution_department_authority_invalid")
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


async def fetch_department_directory() -> DepartmentDirectoryResponse:
    """Fetch the fixed pure endpoint without redirects, ambient proxy, or data leakage."""

    try:
        async with httpx.AsyncClient(
            timeout=PURE_DEPARTMENT_DIRECTORY_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", PURE_DEPARTMENT_DIRECTORY_URL) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise DepartmentDirectoryError("department_directory_upstream_unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_DIRECTORY_RESPONSE_BYTES:
                        raise DepartmentDirectoryError("department_directory_upstream_unavailable")
                    body.extend(chunk)
        return normalize_department_directory(json.loads(body))
    except DepartmentDirectoryError:
        raise
    except httpx.TimeoutException as exc:
        raise DepartmentDirectoryError("department_directory_timeout") from exc
    except (httpx.HTTPError, UnicodeError, ValueError, RecursionError) as exc:
        raise DepartmentDirectoryError("department_directory_upstream_unavailable") from exc


async def validate_profile_department_authorities(values: list[str]) -> str | None:
    """Return the bounded Agent Profile ACL membership result."""

    if not values:
        return None
    try:
        directory = await fetch_department_directory()
        validate_distribution_department_authorities(values, directory)
    except DepartmentDirectoryError as exc:
        if str(exc) == "capability_distribution_department_authority_invalid":
            return "invalid"
        return "unavailable"
    return None
