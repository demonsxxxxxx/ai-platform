import importlib.util
import sys
import zipfile
from pathlib import Path


def load_verify_multiuser_poc():
    path = Path(__file__).resolve().parents[1] / "tools" / "verify_multiuser_poc.py"
    spec = importlib.util.spec_from_file_location("verify_multiuser_poc", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["verify_multiuser_poc"] = module
    spec.loader.exec_module(module)
    return module


def test_default_sample_docx_contains_translatable_text(tmp_path):
    module = load_verify_multiuser_poc()
    sample_path = tmp_path / "sample.docx"

    module.write_minimal_docx(sample_path)

    with zipfile.ZipFile(sample_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "This document contains text" in document_xml
    assert "请将这段中文内容翻译为英文" in document_xml
