from pathlib import Path
import importlib.util

import openpyxl

ROOT = Path(__file__).resolve().parents[1]


def load_fill_excel_module():
    script = ROOT / "skills" / "audit-finding-rca" / "scripts" / "fill_excel.py"
    spec = importlib.util.spec_from_file_location("audit_finding_rca_fill_excel", script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_finding_rca_scan_and_fill_creates_output_columns_without_overwriting(tmp_path):
    module = load_fill_excel_module()
    source = tmp_path / "audit-findings.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["缺陷描述", "客户法规引用"])
    ws.append(["SOP 未明确批记录自动化系统变更后的同步评估要求", "GMP 第八章"])
    ws.append(["设备确认报告中未覆盖报警确认", "ICH Q7 §12"])
    wb.save(source)

    scan = module.scan_empty_rows(source)
    assert scan["empty_count"] == 2
    assert scan["rca_created"] is True
    assert scan["capa_created"] is True

    output_dir = tmp_path / "output"
    result = module.fill_excel(
        source,
        {
            "2": {"rca": "RCA-2", "capa": "CAPA-2"},
            "3": {"rca": "RCA-3", "capa": "CAPA-3"},
        },
        output_dir,
    )

    assert Path(result["output_path"]).is_file()
    original = openpyxl.load_workbook(source).active
    assert original.max_column == 2

    filled = openpyxl.load_workbook(result["output_path"]).active
    assert filled.cell(row=1, column=3).value == "原因分析 (RCA)"
    assert filled.cell(row=1, column=4).value == "整改计划 (CAPA)"
    assert filled.cell(row=2, column=3).value == "RCA-2"
    assert filled.cell(row=2, column=4).value == "CAPA-2"


def test_audit_finding_rca_fill_skips_existing_cells(tmp_path):
    module = load_fill_excel_module()
    source = tmp_path / "audit-findings-existing.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["缺陷描述", "原因分析", "整改计划"])
    ws.append(["SOP 记录字段有待补充", "已有RCA", None])
    wb.save(source)

    result = module.fill_excel(
        source,
        {"2": {"rca": "新RCA", "capa": "新CAPA"}},
        tmp_path / "output",
    )

    filled = openpyxl.load_workbook(result["output_path"]).active
    assert filled.cell(row=2, column=2).value == "已有RCA"
    assert filled.cell(row=2, column=3).value == "新CAPA"
    assert result["rca_filled"] == 0
    assert result["capa_filled"] == 1
