from __future__ import annotations


# SDK clients require a non-empty credential even though authentication is
# performed by the platform model proxy.
OPENSANDBOX_MODEL_CREDENTIAL_SENTINEL = "opensandbox-sdk-sentinel"


def prepare_opensandbox_executor_environment(
    environment: dict[str, str],
    *,
    forward_model_credentials: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    if forward_model_credentials:
        raise ValueError("OpenSandbox model credential forwarding is not supported")

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
            "OPENAI_API_KEY": OPENSANDBOX_MODEL_CREDENTIAL_SENTINEL,
            "ANTHROPIC_AUTH_TOKEN": OPENSANDBOX_MODEL_CREDENTIAL_SENTINEL,
        }
    )
    return filtered_environment, dict(filtered_environment)
