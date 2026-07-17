from app.tool_policy_readiness import build_tool_policy_readiness, render_tool_policy_readiness_markdown


def test_tool_policy_readiness_truthfully_records_only_allow_and_deny_outcomes():
    readiness = build_tool_policy_readiness()

    assert readiness["schema_version"] == "ai-platform.tool-policy-readiness.v2"
    assert readiness["decision_options"] == []
    assert readiness["summary"]["ask_cases"] == 0
    assert {case["classification"] for case in readiness["taxonomy_cases"]} == {"allow", "deny"}
    assert all(case["expected_reason"] != "tool_permission_required" for case in readiness["taxonomy_cases"])


def test_tool_policy_readiness_markdown_is_secret_safe_and_describes_zero_click_contract():
    markdown = render_tool_policy_readiness_markdown(build_tool_policy_readiness())

    assert "Zero-click Tool Policy" in markdown
    assert "allow" in markdown
    assert "ask" not in markdown
    assert "permission_request_id" not in markdown
