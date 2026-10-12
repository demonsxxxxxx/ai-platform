"""Selection policy for unanswered ordinary Run questions."""

import pytest

from app.runs.domain import inputs


def question(options, *, multi=False):
    return {"question": "How should we continue?", "header": "Next",
            "options": [{"label": label, "description": ""} for label in options],
            "multiSelect": multi}


@pytest.mark.parametrize("labels,multi,expected", [
    (["First", "Second (Recommended)"], False, "o1"),
    (["First", "Second（推荐）"], False, "o1"),
    (["First (Not recommended)", "Second"], False, "o0"),
    (["First", "Second"], True, ["o0"]),
    (["First (Recommended)", "Second (recommended)", "Third"], True, ["o0", "o1"]),
])
def test_default_answers_use_recommendations_then_first_option(labels, multi, expected):
    canonical = inputs.canonicalize_questions([question(labels, multi=multi)], sanitize_text=lambda value: value)
    assert inputs.default_question_answers(canonical) == {"q0": expected}


def test_recommendation_survives_display_redaction_and_recanonicalization():
    raw = question(["First", "/app/private (Recommended)"])
    canonical = inputs.canonicalize_questions([raw], sanitize_text=lambda value: "" if "/app/" in value else value)
    assert canonical[0]["options"][1] == {"key": "o1", "label": "Option 2", "description": "", "recommended": True}
    assert inputs.canonicalize_questions(canonical, sanitize_text=lambda value: value) == canonical
    assert inputs.default_question_answers(canonical) == {"q0": "o1"}


def test_no_options_in_any_question_requires_input_instead_of_a_partial_answer():
    raw = [question(["First"]), question([])]
    canonical = inputs.canonicalize_questions(raw, sanitize_text=lambda value: value)
    assert inputs.default_question_answers(canonical) is None


def test_explicit_false_recommendation_remains_false_after_recanonicalization():
    raw = question(["First", "Second (Recommended)"])
    raw["options"][1]["recommended"] = False
    canonical = inputs.canonicalize_questions([raw], sanitize_text=lambda value: value)
    assert inputs.canonicalize_questions(canonical, sanitize_text=lambda value: value) == canonical
    assert inputs.default_question_answers(canonical) == {"q0": "o0"}


def test_recommendation_metadata_requires_a_boolean():
    raw = question(["First"])
    raw["options"][0]["recommended"] = "true"
    with pytest.raises(inputs.RunInputError, match="run_input_question_invalid"):
        inputs.canonicalize_questions([raw], sanitize_text=lambda value: value)


def test_projected_recommendation_passes_the_strict_callback_request_boundary():
    from app.execution.infrastructure.harness.claude.interaction import _project_questions
    from app.runs.transport.inputs import RunInputsCallbackRequest

    projected = _project_questions([question(["First", "/app/private (Recommended)"])],
                                   lambda value: "" if "/app/" in value else value)
    request = RunInputsCallbackRequest.model_validate({
        "operation": "question", "run_id": "run", "attempt_id": "attempt",
        "callback_token_id": "synthetic", "question_id": "question", "questions": projected,
    })
    assert request.model_dump(exclude_none=True)["questions"][0]["options"][1]["recommended"] is True
