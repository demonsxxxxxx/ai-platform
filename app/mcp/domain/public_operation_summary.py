"""Administrator-declared public business object projection for MCP recovery."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.kernel.memory_redaction import is_sensitive_redaction_key, redact_memory_text
from app.mcp.domain.tool_references import is_valid_mcp_public_tool_name

_PRIVATE_FIELD = re.compile(r"account|username|userid|email|phone|credential|password|secret|token|path|directory|filename|storage|账号|账户|密码|密钥|路径", re.I)
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def _public_name(value: Any, maximum: int) -> bool:
    # Declaration is the disclosure authority; lexical checks only reject unsafe values.
    return (isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()
            and all(not unicodedata.category(ch).startswith("C") for ch in value)
            and not any(ch in value for ch in '/\\@:=<>`{}[]')
            and not _PRIVATE_FIELD.search(value) and redact_memory_text(value, mode="strict") == value)


def validate_public_operation_summaries(value: Any) -> dict[str, dict[str, Any]]:
    code = "mcp_public_operation_declaration_invalid"
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError(code)
    result = {}
    for tool, declaration in value.items():
        if (not isinstance(tool, str) or not is_valid_mcp_public_tool_name(tool)
            or not isinstance(declaration, dict)
            or set(declaration) != {"action_label", "object_label", "object_path"}
            or not _public_name(declaration.get("action_label"), 80)
            or not _public_name(declaration.get("object_label"), 80)):
            raise ValueError(code)
        path = declaration["object_path"]
        if (not isinstance(path, list) or not 1 <= len(path) <= 8
            or any(not isinstance(part, str) or not _FIELD.fullmatch(part)
                   or is_sensitive_redaction_key(part) or _PRIVATE_FIELD.search(part.replace("_", ""))
                   for part in path)):
            raise ValueError(code)
        result[tool] = {**declaration, "object_path": list(path)}
    return result


def public_operation_summary(declaration: Any, native_input: Any) -> str:
    if declaration is None:
        raise ValueError("recovery_operation_public_object_metadata_missing")
    try:
        declaration = validate_public_operation_summaries({"tool": declaration})["tool"]
    except ValueError:
        raise ValueError("recovery_operation_public_object_metadata_missing") from None
    value = native_input
    for part in declaration["object_path"]:
        if not isinstance(value, dict) or part not in value:
            raise ValueError("recovery_operation_public_object_field_missing")
        value = value[part]
    if not _public_name(value, 160):
        raise ValueError("recovery_operation_public_object_value_invalid")
    return f'{declaration["action_label"]}；{declaration["object_label"]}：{value}'
