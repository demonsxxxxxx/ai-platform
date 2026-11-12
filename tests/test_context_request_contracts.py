"""Context-owned HTTP request validation and composition contracts."""

import pytest


# Transport ownership must preserve the public request contract independently
# of route persistence fakes and authorization setup.
@pytest.mark.parametrize(
    "name,payload,expected",
    [
        ("ContextSnapshotRequest", {}, {"context_kind": "executor", "included_message_ids": [],
         "included_file_ids": [], "included_artifact_ids": [], "included_memory_record_ids": [],
         "redaction_summary": {}, "payload": {}}),
        ("ShareContextSnapshotRequest", {"target_session_id": "session-a"},
         {"share_kind": "share", "target_session_id": "session-a", "payload": {}, "rollback": {}}),
        ("MemoryRecordRequest", {"content": "note"}, {"workspace_id": "default", "agent_id": None,
         "session_id": None, "record_type": "session_summary", "content": "note", "metadata": {}}),
        ("MemoryPolicyRequest", {}, {"workspace_id": "default", "agent_id": None,
         "memory_enabled": True, "long_term_memory_enabled": False, "retention_days": 90,
         "redaction_mode": "standard", "reason": ""}),
        ("MemoryRedactionPreviewRequest", {}, {"workspace_id": "default", "agent_id": None,
         "redaction_mode": "standard", "content": "", "metadata": {}, "reason": ""}),
    ],
)
def test_context_request_contract_defaults_extra_and_mutable_isolation(name, payload, expected):
    from pydantic import ValidationError

    import app.models as legacy_models
    from app.bootstrap import context_requests
    from app.context.transport import requests

    model = getattr(requests, name)
    assert getattr(context_requests, name) is model
    assert not hasattr(legacy_models, name)
    first, second = model(**payload), model(**payload)
    assert first.model_dump() == expected
    for field, value in first.model_dump().items():
        if isinstance(value, list):
            getattr(first, field).append("new")
            assert getattr(second, field) == expected[field]
        elif isinstance(value, dict):
            getattr(first, field)["new"] = True
            assert getattr(second, field) == expected[field]
    with pytest.raises(ValidationError) as exc:
        model(**payload, unknown=True)
    assert exc.value.errors()[0]["type"] == "extra_forbidden"
    schema = model.model_json_schema()
    assert schema["title"] == name
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("field", ["included_message_ids", "included_file_ids",
                                   "included_artifact_ids", "included_memory_record_ids"])
def test_context_snapshot_request_safe_id_lists(field):
    from pydantic import ValidationError

    from app.context.transport.requests import ContextSnapshotRequest

    assert getattr(ContextSnapshotRequest(**{field: ["a" * 128, "same", "same"]}), field) == [
        "a" * 128, "same", "same",
    ]
    for value in ("", "../escape", " spaced ", "a" * 129):
        with pytest.raises(ValidationError, match=f"{field} contains unsupported characters"):
            ContextSnapshotRequest(**{field: [value]})


@pytest.mark.parametrize("name,field,payload", [
    ("ShareContextSnapshotRequest", "target_session_id", {"target_session_id": "session-a"}),
    ("MemoryRecordRequest", "workspace_id", {"content": "note"}),
    ("MemoryRecordRequest", "agent_id", {"content": "note"}),
    ("MemoryRecordRequest", "session_id", {"content": "note"}),
    ("MemoryPolicyRequest", "workspace_id", {}),
    ("MemoryPolicyRequest", "agent_id", {}),
    ("MemoryRedactionPreviewRequest", "workspace_id", {}),
    ("MemoryRedactionPreviewRequest", "agent_id", {}),
])
def test_context_request_scalar_id_validation(name, field, payload):
    from pydantic import ValidationError

    from app.context.transport import requests

    model = getattr(requests, name)
    assert getattr(model(**{**payload, field: "a" * 128}), field) == "a" * 128
    for value in ("../escape", " spaced ", "a" * 129):
        with pytest.raises(ValidationError, match=f"{field} contains unsupported characters"):
            model(**{**payload, field: value})
    if field in {"agent_id", "session_id"}:
        for value in (None, ""):
            assert getattr(model(**{**payload, field: value}), field) == value


@pytest.mark.parametrize("name,field,minimum,maximum,payload", [
    ("MemoryRecordRequest", "content", 1, 16000, {}),
    ("MemoryRedactionPreviewRequest", "content", 0, 16000, {}),
    ("MemoryPolicyRequest", "reason", 0, 2000, {}),
    ("MemoryRedactionPreviewRequest", "reason", 0, 2000, {}),
])
def test_context_request_text_boundaries(name, field, minimum, maximum, payload):
    from pydantic import ValidationError

    from app.context.transport import requests

    model = getattr(requests, name)
    for size in (minimum, maximum):
        assert getattr(model(**{**payload, field: "x" * size}), field) == "x" * size
    with pytest.raises(ValidationError) as exc:
        model(**{**payload, field: "x" * (maximum + 1)})
    assert exc.value.errors()[0]["type"] == "string_too_long"
    if minimum:
        with pytest.raises(ValidationError) as exc:
            model(**{**payload, field: ""})
        assert exc.value.errors()[0]["type"] == "string_too_short"


def test_context_memory_retention_request_boundaries():
    from pydantic import ValidationError

    from app.context.transport.requests import MemoryPolicyRequest

    for value in (1, 3650):
        assert MemoryPolicyRequest(retention_days=value).retention_days == value
    for value, kind in ((0, "greater_than_equal"), (3651, "less_than_equal")):
        with pytest.raises(ValidationError) as exc:
            MemoryPolicyRequest(retention_days=value)
        assert exc.value.errors()[0]["type"] == kind
