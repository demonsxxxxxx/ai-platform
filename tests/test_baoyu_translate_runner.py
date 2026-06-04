import importlib.util
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def load_runner_module():
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "baoyu-translate"
        / "scripts"
        / "run_translation.py"
    )
    sys.path.insert(0, str(runner_path.parent))
    spec = importlib.util.spec_from_file_location("baoyu_translate_runner_for_test", runner_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(runner_path.parent))
    return module


def write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>"
        ),
        "word/document.xml": document_xml,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def read_document_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def test_translation_runner_writes_translated_docx_and_report(monkeypatch, tmp_path):
    runner = load_runner_module()
    input_docx = tmp_path / "sample.docx"
    output_dir = tmp_path / "output"
    write_minimal_docx(input_docx, ["项目名称：AI 平台文档翻译冒烟测试。", "术语：样品管理、审计追踪。"])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_translation.py",
            str(input_docx),
            str(output_dir),
            "--target-language",
            "English",
            "--original-filename",
            "sample.docx",
        ],
    )

    exit_code = runner.main()

    assert exit_code == 0
    translated_docx = output_dir / "sample_translated.docx"
    assert translated_docx.is_file()
    document_xml = read_document_text(translated_docx)
    assert "AI Platform document translation smoke test" in document_xml
    assert "Sample Management" in document_xml
    assert not (output_dir / "sample_translation_report.txt").exists()


def test_translation_runner_rejects_invalid_docx_without_traceback(monkeypatch, tmp_path, capsys):
    runner = load_runner_module()
    input_docx = tmp_path / "broken.docx"
    input_docx.write_bytes(b"not a zip file")
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_translation.py",
            str(input_docx),
            str(output_dir),
            "--target-language",
            "English",
            "--original-filename",
            "broken.docx",
        ],
    )

    exit_code = runner.main()
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "invalid_docx_package" in captured.err
    assert "Traceback" not in captured.err
    assert not output_dir.exists()


def test_translation_runner_rejects_unsafe_zip_member(monkeypatch, tmp_path, capsys):
    runner = load_runner_module()
    input_docx = tmp_path / "unsafe.docx"
    write_minimal_docx(input_docx, ["项目名称：AI 平台文档翻译冒烟测试。"])
    with zipfile.ZipFile(input_docx, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../evil.txt", "evil")
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_translation.py",
            str(input_docx),
            str(output_dir),
            "--target-language",
            "English",
            "--original-filename",
            "unsafe.docx",
        ],
    )

    exit_code = runner.main()
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "unsafe_docx_member" in captured.err
    assert not output_dir.exists()


def test_translation_runner_requires_output_directory_name(monkeypatch, tmp_path, capsys):
    runner = load_runner_module()
    input_docx = tmp_path / "sample.docx"
    write_minimal_docx(input_docx, ["项目名称：AI 平台文档翻译冒烟测试。"])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_translation.py",
            str(input_docx),
            str(tmp_path / "custom"),
            "--target-language",
            "English",
            "--original-filename",
            "sample.docx",
        ],
    )

    exit_code = runner.main()
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "output_dir_must_be_output" in captured.err
