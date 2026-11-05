from importlib.metadata import version
from pathlib import Path

import anydoc
import pytest

from app.execution.domain.document_plugin import (
    DOCUMENT_PLUGIN_INPUT_KEY, DOCUMENT_PLUGIN_REVISION, DOCUMENT_PLUGIN_SDK_SKILLS,
    admit_document_plugin, document_plugin_skill_names,
)


FIXTURES = Path(__file__).parent / "fixtures" / "sandbox-documents"


def test_document_plugin_admission_overwrites_caller_and_does_not_expand_experts():
    from app.projection_redaction import sanitize_user_control_input
    from app.runs.infrastructure.capability_admission_postgres import normalize_run_input_for_enqueue

    source = {"message": "make a workbook", DOCUMENT_PLUGIN_INPUT_KEY: "untrusted"}
    normalized = normalize_run_input_for_enqueue(source, redact_public=True)
    assert DOCUMENT_PLUGIN_INPUT_KEY not in normalized
    admitted = admit_document_plugin(normalized, "harness_chat")
    assert admitted[DOCUMENT_PLUGIN_INPUT_KEY] == DOCUMENT_PLUGIN_REVISION
    assert document_plugin_skill_names(DOCUMENT_PLUGIN_REVISION) == list(DOCUMENT_PLUGIN_SDK_SKILLS)
    assert document_plugin_skill_names(None) == []
    assert DOCUMENT_PLUGIN_INPUT_KEY not in admit_document_plugin(source, "skill")
    assert DOCUMENT_PLUGIN_INPUT_KEY not in admit_document_plugin(source, "harness_chat", agent_profile={"agent_id": "synthetic-expert"})
    assert DOCUMENT_PLUGIN_INPUT_KEY not in sanitize_user_control_input(admitted)
    with pytest.raises(ValueError, match="document_plugin_revision_invalid"):
        document_plugin_skill_names("untrusted")


def test_document_plugin_archive_integrity_and_exact_skill_inventory(monkeypatch, tmp_path):
    import hashlib
    import zipfile
    from app.execution.domain.document_plugin import DOCUMENT_PLUGIN_SKILLS
    from app.execution.infrastructure import document_plugin

    archive = tmp_path / "upstream.zip"
    with zipfile.ZipFile(archive, "w") as source:
        for name in (*DOCUMENT_PLUGIN_SKILLS, "claude-api"):
            for filename in ("SKILL.md", "LICENSE.txt"):
                source.writestr(f"skills-{DOCUMENT_PLUGIN_REVISION}/skills/{name}/{filename}", name + filename)
    destination = tmp_path / "plugin"
    with pytest.raises(ValueError, match="document_plugin_archive_invalid"):
        document_plugin.install_document_plugin(archive, destination)
    assert not destination.exists()
    monkeypatch.setattr(document_plugin, "DOCUMENT_PLUGIN_ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    document_plugin.install_document_plugin(archive, destination)
    assert {path.name for path in (destination / "skills").iterdir()} == set(DOCUMENT_PLUGIN_SKILLS)
    assert (destination / "skills/docx/SKILL.md").read_text() == "docxSKILL.md"
    monkeypatch.setattr(document_plugin, "DOCUMENT_PLUGIN_PATH", destination)
    assert document_plugin.document_plugin_options(DOCUMENT_PLUGIN_REVISION) == [{"type": "local", "path": str(destination)}]
    assert document_plugin.document_plugin_read_allowed(str(destination / "skills/docx/SKILL.md"), DOCUMENT_PLUGIN_REVISION)
    assert not document_plugin.document_plugin_read_allowed(str(destination / "skills/../../private"), DOCUMENT_PLUGIN_REVISION)
    assert not document_plugin.document_plugin_read_allowed(str(destination / "skills/docx/SKILL.md"), None)
    with pytest.raises(ValueError, match="document_plugin_destination_exists"):
        document_plugin.install_document_plugin(archive, destination)


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
