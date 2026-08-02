PLATFORM_MULTI_AGENT_NOT_SUPPORTED = "platform_multi_agent_not_supported"
RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON = "retired_platform_multi_agent_control"

_PLATFORM_MULTI_AGENT_CONTROL_KEYS = {
    "multiagentdispatch",
    "multiagentsteps",
}


def contains_platform_multi_agent_control(input_payload: object) -> bool:
    """Return whether a client payload requests retired platform orchestration."""

    if not isinstance(input_payload, dict):
        return False
    for raw_key, value in input_payload.items():
        key = str(raw_key).replace("_", "").replace("-", "").lower()
        if key in _PLATFORM_MULTI_AGENT_CONTROL_KEYS:
            return True
        execution_mode = str(value or "").replace("_", "").replace("-", "").lower()
        if key == "executionmode" and execution_mode == "multiagent":
            return True
    return False


def contains_persisted_platform_multi_agent_control(input_json: object) -> bool:
    """Check both historical root input and current nested execution input."""

    if contains_platform_multi_agent_control(input_json):
        return True
    if not isinstance(input_json, dict):
        return False
    return contains_platform_multi_agent_control(input_json.get("input"))
