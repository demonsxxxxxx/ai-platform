"""Closed server-derived Run source, recorded in private admission evidence."""
RUN_TRIGGER_INPUT_KEY = "_trusted_run_source"
RUN_TRIGGER_SOURCES = frozenset({"chat_api", "agent_profile_api", "direct_skill", "direct_api", "copy", "retry", "resume"})


def validate_run_trigger(source: str) -> None:
    if source not in RUN_TRIGGER_SOURCES:
        raise ValueError("run_trigger_source_invalid")


def read_run_trigger(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    trigger = value.get(RUN_TRIGGER_INPUT_KEY)
    if not isinstance(trigger, dict) or trigger.get("version") != 1:
        return None
    source = trigger.get("source")
    return source if isinstance(source, str) and source in RUN_TRIGGER_SOURCES else None
