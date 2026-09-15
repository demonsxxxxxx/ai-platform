import pytest

from app.execution.api import artifact_type


@pytest.mark.parametrize("filename", ["report.txt", "summary.md"])
def test_worker_keeps_text_artifact_classification_after_tuple_suffix_check(filename):
    assert artifact_type(filename) == "report_txt"
