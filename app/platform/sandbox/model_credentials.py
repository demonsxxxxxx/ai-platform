from __future__ import annotations


def prepare_opensandbox_executor_environment(
    environment: dict[str, str],
    *,
    forward_model_credentials: bool,
    model_proxy_capability: str,
) -> tuple[dict[str, str], dict[str, str]]:
    if forward_model_credentials:
        raise ValueError("OpenSandbox model credential forwarding is not supported")
    if not model_proxy_capability:
        raise ValueError("OpenSandbox model proxy capability is required")

    filtered_environment = {
        key: value
        for key, value in environment.items()
        if key not in {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "MODEL_CATALOG_JSON",
        }
    }
    filtered_environment.update(
        {
            "OPENAI_API_KEY": model_proxy_capability,
            "ANTHROPIC_AUTH_TOKEN": model_proxy_capability,
        }
    )
    return filtered_environment, dict(filtered_environment)
