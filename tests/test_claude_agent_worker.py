import pytest

from app.executors import claude_agent_worker


@pytest.mark.parametrize("filename", ["report.txt", "summary.md"])
def test_worker_keeps_text_artifact_classification_after_tuple_suffix_check(filename):
    assert claude_agent_worker._artifact_type(filename) == "report_txt"
