import json
from pathlib import Path

import pytest

from app.backend_stage_closure_evidence import find_stage_issue_closure_evidence


def write_closure_evidence(repo_root: Path, **overrides):
    payload = {
        "schema_version": "ai-platform.backend-stage-closure-evidence.v1",
        "backend_stage": "B2 real sandbox usable",
        "issue": "#89",
        "issue_state": "closed",
        "closed_at": "2026-06-18T20:16:00Z",
        "closed_gap": "b2_issue_review_and_closure_evidence",
        "review_status": "reviewed",
        "redaction_scan_status": "passed",
        "linked_prs": [
            {
                "number": 90,
                "url": "https://github.com/demonsxxxxxx/ai-platform/pull/90",
                "merge_commit": "f8a0f3c1168c34663850345d8f30358d435a0134",
            }
        ],
        "closure_comments": [
            {
                "url": "https://github.com/demonsxxxxxx/ai-platform/issues/89#issuecomment-4745786980",
                "summary": "Issue-scoped closure only.",
            }
        ],
        "evidence_refs": [
            "docs/release-evidence/b2-sandbox/f8a0f3c1168c34663850345d8f30358d435a0134/"
            "2026-06-19-211-b2-sandbox-runtime-smoke-f8a0f3c.json"
        ],
        "residual_caveats": [
            "does_not_close_broader_b2_g7_production_hardening_gate"
        ],
        "non_expansion_invariants": {
            "ordinary_user_high_risk_sandbox_allowed": False,
            "ordinary_user_multi_agent_allowed": False,
        },
        "does_not_close_broader_gate": True,
    }
    for key, value in overrides.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value

    path = (
        repo_root
        / "docs/release-evidence/backend-stage-closures/b2-sandbox"
        / "2026-06-18-issue89-b2-closure.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def find_b2_closure(repo_root: Path):
    return find_stage_issue_closure_evidence(
        repo_root,
        issue="#89",
        backend_stage="B2 real sandbox usable",
        closed_gap="b2_issue_review_and_closure_evidence",
    )


def test_stage_issue_closure_evidence_accepts_complete_reviewed_artifact(tmp_path):
    write_closure_evidence(tmp_path)

    evidence = find_b2_closure(tmp_path)

    assert evidence is not None
    assert evidence["closed_gap"] == "b2_issue_review_and_closure_evidence"
    assert evidence["path"].endswith("2026-06-18-issue89-b2-closure.json")


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_refs": None},
        {"evidence_refs": []},
        {"evidence_refs": [""]},
        {"residual_caveats": []},
        {"residual_caveats": [""]},
        {"non_expansion_invariants": {}},
        {"non_expansion_invariants": {"ordinary_user_multi_agent_allowed": "false"}},
        {"linked_prs": []},
        {"closure_comments": []},
        {"review_status": "pending"},
        {"redaction_scan_status": "failed"},
        {"does_not_close_broader_gate": False},
        {"issue_state": "open"},
        {"closed_gap": "wrong_gap"},
        {"backend_stage": "B1 memory/context usable"},
    ],
)
def test_stage_issue_closure_evidence_fails_closed_for_invalid_artifacts(
    tmp_path,
    overrides,
):
    write_closure_evidence(tmp_path, **overrides)

    assert find_b2_closure(tmp_path) is None
