from __future__ import annotations


def prepare_opensandbox_executor_environment(
    environment: dict[str, str],
    *,
    forward_model_credentials: bool,
    model_proxy_capability: str,
) -> tuple[dict[str, str], dict[str, str]]:
    stripped_keys = {"ANTHROPIC_API_KEY", "MODEL_CATALOG_JSON"}
    if not forward_model_credentials:
        if not model_proxy_capability:
            raise ValueError("OpenSandbox model proxy capability is required")
        stripped_keys.update({"OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN"})

    filtered_environment = {
        key: value for key, value in environment.items() if key not in stripped_keys
    }
    if not forward_model_credentials:
        filtered_environment.update(
            {
                "OPENAI_API_KEY": model_proxy_capability,
                "ANTHROPIC_AUTH_TOKEN": model_proxy_capability,
            }
        )
    credential_free_environment = {
        key: value
        for key, value in filtered_environment.items()
        if key not in {"OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
    }
    return filtered_environment, credential_free_environment
