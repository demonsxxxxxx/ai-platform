from importlib.metadata import version
from pathlib import Path

import anydoc
import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "sandbox-documents"


def test_sandbox_document_parser_version_is_pinned():
    assert version("firecrawl-anydoc") == "0.2.4"


@pytest.mark.parametrize(
    ("filename", "marker"),
    [
        ("legacy.doc", "Привет, мир!"),
        ("legacy.xls", "fifteen and a half"),
        ("legacy.ppt", "Notes for the second slide"),
        ("modern.pptx", "Relocated deck title"),
    ],
)
def test_sandbox_document_parser_reads_office_formats_without_hosted_ocr(
    filename, marker
):
    markdown = anydoc.to_markdown(FIXTURES / filename, ocr="reject")

    assert marker in markdown
