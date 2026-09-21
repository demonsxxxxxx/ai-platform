"""Build one bounded, offline-readable administrator Run diagnostic package."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from typing import Any


ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION = "ai-platform.run-diagnostic-export.v1"
ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES = 512 * 1024
ADMIN_DIAGNOSTIC_EXPORT_FILES = (
    "manifest.json",
    "diagnostics.json",
    "README.txt",
)


class AdminDiagnosticExportTooLarge(ValueError):
    """Raised when the fixed export payload cannot fit the product budget."""


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported_admin_diagnostic_export_value:{type(value).__name__}")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=_json_default,
    ).encode("utf-8")


def _readme_text() -> str:
    return """ai-platform Run 脱敏诊断包

此包只包含管理员诊断接口已经投影和脱敏的单 Run 证据。

- diagnostics.json：Run、Attempt、最早留存异常、后续处理和完整性信息。
- manifest.json：快照身份、版本、文件摘要和内容预算。
- 最早留存异常不等于已经证明的根因。
- 缺失值表示未采集、历史格式或当前版本无法解释，不应按 0 推断。
- 包内不包含原始提示词、文件正文、完整 SDK 载荷、凭据、内部连接地址或完整运行路径。
"""


def build_admin_diagnostic_export(
    *,
    diagnostics: dict[str, Any],
    export_id: str,
    generated_at: datetime,
) -> bytes:
    """Return a deterministic-shape ZIP from the safe administrator projection."""

    diagnostic_bytes = _json_bytes(diagnostics)
    readme_bytes = _readme_text().encode("utf-8")
    diagnostic_versions = diagnostics.get("versions")
    manifest = {
        "schema_version": ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
        "export_id": export_id,
        "generated_at": generated_at.isoformat(),
        "run_id": diagnostics.get("run", {}).get("run_id")
        if isinstance(diagnostics.get("run"), dict)
        else None,
        "diagnostic_id": diagnostics.get("diagnostic_id"),
        "diagnostic_revision": int(diagnostics.get("revision") or 0),
        "coverage": diagnostics.get("coverage"),
        "versions": {
            "recorded_diagnostics": diagnostic_versions
            if isinstance(diagnostic_versions, dict)
            else {},
            "export_schema": ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
        },
        "files": {
            "diagnostics.json": {
                "bytes": len(diagnostic_bytes),
                "sha256": hashlib.sha256(diagnostic_bytes).hexdigest(),
            },
            "README.txt": {
                "bytes": len(readme_bytes),
                "sha256": hashlib.sha256(readme_bytes).hexdigest(),
            },
        },
        "limits": {
            "uncompressed_total_bytes": ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES,
        },
    }
    manifest_bytes = _json_bytes(manifest)
    total_bytes = len(manifest_bytes) + len(diagnostic_bytes) + len(readme_bytes)
    if total_bytes > ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES:
        raise AdminDiagnosticExportTooLarge("admin_run_diagnostic_export_too_large")

    package = io.BytesIO()
    with zipfile.ZipFile(
        package,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("diagnostics.json", diagnostic_bytes)
        archive.writestr("README.txt", readme_bytes)
    return package.getvalue()


__all__ = [
    "ADMIN_DIAGNOSTIC_EXPORT_FILES",
    "ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES",
    "ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION",
    "AdminDiagnosticExportTooLarge",
    "build_admin_diagnostic_export",
]
