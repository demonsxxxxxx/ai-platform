"""Fixed platform document capability admitted independently of tenant Skills."""

from __future__ import annotations

from typing import Any

DOCUMENT_PLUGIN_INPUT_KEY = "_document_plugin_revision"
DOCUMENT_PLUGIN_REVISION = "ca45fc496a3436251f191354091d557da31c15b5"
DOCUMENT_PLUGIN_ARCHIVE_SHA256 = "25fec008c68956c3bc197e43072acf1b00c8281ecd2883f82acc94ae8ebb5774"
DOCUMENT_PLUGIN_SKILLS = ("docx", "xlsx", "pptx", "pdf")
DOCUMENT_PLUGIN_SDK_SKILLS = tuple(f"document-skills:{name}" for name in DOCUMENT_PLUGIN_SKILLS)


def admit_document_plugin(run_input: dict[str, Any], execution_kind: str, *, agent_profile: object = None) -> dict[str, Any]:
    """Only initial server admission may grant the image-provided document suite."""
    result = dict(run_input)
    result.pop(DOCUMENT_PLUGIN_INPUT_KEY, None)
    if execution_kind == "harness_chat" and not agent_profile:
        result[DOCUMENT_PLUGIN_INPUT_KEY] = DOCUMENT_PLUGIN_REVISION
    return result


def document_plugin_skill_names(revision: object) -> list[str]:
    if revision is None:
        return []
    if revision != DOCUMENT_PLUGIN_REVISION:
        raise ValueError("document_plugin_revision_invalid")
    return list(DOCUMENT_PLUGIN_SDK_SKILLS)


def document_plugin_tool_subject(revision: object) -> list[dict[str, Any]]:
    names = document_plugin_skill_names(revision)
    if not names:
        return []
    return [{
        "identity": "Skill", "registered": True, "declared": True,
        "active": True, "distributed": True, "identity_authorized": True,
        "object_authorized": True, "parameters_authorized": True,
        "risk_level": "low", "write_capable": False,
        "allowed_skill_names": names,
        "public_skill_labels": dict(zip(names, ("Word 文档", "Excel 表格", "PowerPoint 演示文稿", "PDF 文档"))),
    }]
