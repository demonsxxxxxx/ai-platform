"""Build one bounded, offline-readable administrator Run diagnostic package."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from datetime import date, datetime
from typing import Any


ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION = "ai-platform.run-diagnostic-export.v2"
ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES = 512 * 1024
ADMIN_DIAGNOSTIC_EXPORT_FILES = (
    "manifest.json",
    "diagnostics.json",
    "README.txt",
    "trajectory.md",
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

- diagnostics.json：Run、Attempt、异常与处理链、用量、阶段时间线、事件关联、冻结配置和租约状态。
- debug.events 只含事件类型、序号、时间及关联摘要，不含公开回答正文或工具输入/输出。
- manifest.json：快照身份、版本、文件摘要、内容预算及导出裁剪说明。
- trajectory.md：与 diagnostics.json 同一快照、同一裁剪范围的离线阅读摘要。
- v2 新增 trajectory.md；diagnostics.json 保留 v1 字段，旧 reader 可继续读取该文件。
- debug.query_component 是查询时版本；sdk.debug 中的版本才是执行时采集事实。缺失版本不从当前服务倒填。
- 时间戳含时区，耗时单位为毫秒，字节数是 UTF-8 JSON 或明确命名的编码字节；阶段可能重叠，不能相加。
- coverage 表示异常采集覆盖，不代表网络、代理、供应商内部证据完整。debug.missing_evidence 列出外部证据缺口。
- 最早留存异常不等于已经证明的根因。
- 缺失值表示未采集、历史格式或当前版本无法解释，不应按 0 推断。
- 包内不包含原始提示词、文件正文、完整 SDK 载荷、凭据、内部连接地址或完整运行路径。
"""


def _trajectory_markdown(diagnostics: dict[str, Any]) -> bytes:
    def text(value: object) -> str:
        return str(value if value is not None else "未记录").replace("\n", " ").replace("|", "\\|")
    run = diagnostics.get("run") or {}
    debug = diagnostics.get("debug") or {}
    lines = ["# Run 脱敏轨迹摘要", "", f"Run：{text(run.get('run_id'))}",
             f"诊断 revision：{diagnostics.get('revision', 0)} · 覆盖：{text(diagnostics.get('coverage'))}",
             "", "同一快照的保留事件；顺序表示平台记录顺序，不证明因果。阶段耗时可能重叠。", "",
             "| 序号 | 时间 | 事件 | Attempt | 调用 | 结果 |", "| --- | --- | --- | --- | --- | --- |"]
    for event in debug.get("events", []):
        lines.append("| " + " | ".join(text(event.get(key)) for key in ("sequence", "recorded_at", "source_type", "attempt_id", "operation_id", "outcome")) + " |")
    if not debug.get("events"):
        lines.append("\n没有保留的公开类型化事件。")
    lines += ["", "## 证据缺口", f"事件截断：{text(debug.get('events_truncated'))}",
              "导出省略：" + text(debug.get("export_omissions", {})),
              "未采集：" + text(debug.get("missing_evidence", [])),
              "", "不包含提示词、消息分块正文、工具私有载荷或文件内容。"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_admin_diagnostic_export(
    *,
    diagnostics: dict[str, Any],
    export_id: str,
    generated_at: datetime,
) -> bytes:
    """Return a deterministic-shape ZIP from the safe administrator projection."""

    diagnostics = deepcopy(diagnostics)
    # Keep failure evidence; trim optional history explicitly before refusing the package.
    debug = diagnostics.get("debug")
    if isinstance(debug, dict):
        for key, container in (("events", debug), ("stages", debug.get("investigation", {})),
                               ("leases", debug.get("runtime") or {}), ("skill_snapshots", debug)):
            values = container.get(key) if isinstance(container, dict) else None
            while isinstance(values, list) and len(values) > 8 and len(_json_bytes(diagnostics)) > ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES - 16 * 1024:
                retained = max(8, len(values) // 2)
                omitted = len(values) - retained
                container[key] = values = values[-retained:]
                losses = debug.setdefault("export_omissions", {})
                losses[key] = losses.get(key, 0) + omitted
    # Both files derive from the already-trimmed snapshot. Account for summary
    # duplication without widening the existing 512 KiB package budget.
    while isinstance(debug, dict) and len(debug.get("events", [])) > 8 and len(_json_bytes(diagnostics)) + len(_trajectory_markdown(diagnostics)) > ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES - 16 * 1024:
        values = debug["events"]
        retained = max(8, len(values) // 2)
        debug.setdefault("export_omissions", {})["events"] = debug.get("export_omissions", {}).get("events", 0) + len(values) - retained
        debug["events"] = values[-retained:]
    diagnostic_bytes = _json_bytes(diagnostics)
    trajectory_bytes = _trajectory_markdown(diagnostics)
    readme_bytes = _readme_text().encode("utf-8")
    diagnostic_versions = diagnostics.get("versions")
    manifest = {
        "schema_version": ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
        "export_id": export_id,
        "reader_compatibility": {"diagnostics_json": "v1_fields_preserved", "added_files": ["trajectory.md"]},
        "generated_at": generated_at.isoformat(),
        "run_id": diagnostics.get("run", {}).get("run_id")
        if isinstance(diagnostics.get("run"), dict)
        else None,
        "diagnostic_id": diagnostics.get("diagnostic_id"),
        "diagnostic_revision": int(diagnostics.get("revision") or 0),
        "coverage": diagnostics.get("coverage"),
        "export_omissions": debug.get("export_omissions", {}) if isinstance(debug, dict) else {},
        "missing_evidence": debug.get("missing_evidence", []) if isinstance(debug, dict) else ["run_debug_snapshot_not_collected"],
        "versions": {
            "recorded_diagnostics": diagnostic_versions
            if isinstance(diagnostic_versions, dict)
            else {},
            "export_schema": ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION,
        },
        "files": {
            "trajectory.md": {"bytes": len(trajectory_bytes), "sha256": hashlib.sha256(trajectory_bytes).hexdigest()},
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
    total_bytes = len(manifest_bytes) + len(diagnostic_bytes) + len(readme_bytes) + len(trajectory_bytes)
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
        archive.writestr("trajectory.md", trajectory_bytes)
    return package.getvalue()


__all__ = [
    "ADMIN_DIAGNOSTIC_EXPORT_FILES",
    "ADMIN_DIAGNOSTIC_EXPORT_MAX_UNCOMPRESSED_BYTES",
    "ADMIN_DIAGNOSTIC_EXPORT_SCHEMA_VERSION",
    "AdminDiagnosticExportTooLarge",
    "build_admin_diagnostic_export",
]
