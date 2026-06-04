import importlib.util
import sys
from pathlib import Path


def load_runner_module():
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "qa-file-reviewer"
        / "scripts"
        / "run_qa_review.py"
    )
    sys.path.insert(0, str(runner_path.parent))
    spec = importlib.util.spec_from_file_location("qa_file_reviewer_runner_for_test", runner_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(runner_path.parent))
    return module


def test_quality_gate_report_only_review_returns_success(monkeypatch, tmp_path):
    runner = load_runner_module()
    input_docx = tmp_path / "sample.docx"
    input_docx.write_bytes(b"docx bytes are not read in this unit test")
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_qa_review.py",
            str(input_docx),
            str(output_dir),
            "--with-comments",
            "--original-filename",
            "sample.docx",
        ],
    )
    monkeypatch.setattr(runner, "ensure_utf8_stdio", lambda: None)
    monkeypatch.setattr(runner, "load_document", lambda _path: object())
    monkeypatch.setattr(runner, "extract_document_map", lambda _input, _output: [])
    monkeypatch.setattr(runner, "get_review_paragraphs", lambda _document_map: [])
    monkeypatch.setattr(runner, "detect_current_project", lambda _source, _paragraphs: "")
    monkeypatch.setattr(
        runner,
        "execute_branches",
        lambda _doc, _paragraphs, _project: (
            [{"branch": "format", "status": "failed", "error": "format branch unavailable"}],
            [],
        ),
    )
    monkeypatch.setattr(runner, "adjudicate_issues_before_comment_plan", lambda issues, _paragraphs: issues)
    monkeypatch.setattr(runner, "sync_manifest_issue_counts", lambda _manifest, _issues: None)
    monkeypatch.setattr(runner, "build_human_review_queue", lambda _issues, _manifest: [])
    monkeypatch.setattr(runner, "build_comment_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner,
        "build_review_payload",
        lambda *_args, **_kwargs: {
            "summary": {"total_issues": 0, "by_severity": {}},
            "issues": [],
            "human_review_queue": [],
            "commenting": {},
            "artifacts": {},
            "review_time": "2026-06-01T00:00:00",
            "document_display_name": "sample.docx",
            "document": "sample.docx",
        },
    )
    monkeypatch.setattr(
        runner,
        "validate_pipeline",
        lambda _review_json, _validation_json: {
            "passed": False,
            "errors": ["failed branches detected: format"],
        },
    )
    monkeypatch.setattr(runner, "build_detailed_report", lambda *_args, **_kwargs: "report only\n")
    monkeypatch.setattr(runner, "print_summary", lambda _payload, _validation: None)

    exit_code = runner.main()

    assert exit_code == 0
    assert (output_dir / "sample_审核详细报告.txt").read_text(encoding="utf-8") == "report only\n"
    assert not (output_dir / "review_result.json").exists()
    assert not (output_dir / "validation_report.json").exists()
