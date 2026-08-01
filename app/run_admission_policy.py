PLATFORM_MULTI_AGENT_NOT_SUPPORTED = "platform_multi_agent_not_supported"

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
