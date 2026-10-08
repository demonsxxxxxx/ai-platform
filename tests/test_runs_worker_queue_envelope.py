import pytest

from app.runs.api import InvalidLeasedQueueEnvelope, parse_leased_queue_envelope
from app.validation import assert_safe_id


def test_leased_queue_envelope_validates_attempt_before_parsing_business_payload():
    observed = []

    def parse_payload(raw):
        observed.append(raw)
        return raw

    parameters = {
        "attempt_field": "_queue_attempt_id",
        "validate_attempt_id": assert_safe_id,
        "parse_payload": parse_payload,
    }
    for invalid in ({"message": "hi"}, {"_queue_attempt_id": "bad/attempt"}):
        with pytest.raises(InvalidLeasedQueueEnvelope):
            parse_leased_queue_envelope(invalid, **parameters)
    assert observed == []

    original = {"_queue_attempt_id": "attempt-1", "message": "hi"}
    envelope = parse_leased_queue_envelope(original, **parameters)
    assert envelope.attempt_id == "attempt-1"
    assert envelope.payload == {"message": "hi"}
    assert observed == [{"message": "hi"}]
    assert "_queue_attempt_id" in original
