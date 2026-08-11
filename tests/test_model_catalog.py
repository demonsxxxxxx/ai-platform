from types import SimpleNamespace

import pytest

from app.model_catalog import (
    DEFAULT_MAX_INPUT_TOKENS,
    MODEL_CATALOG_NOT_CONFIGURED,
    build_model_catalog,
    resolve_model_selection,
)


def settings(**overrides):
    values = {
        "model_catalog_json": "",
        "llm_gateway_provider": "openai_compatible",
        "claude_agent_model": "",
        "anthropic_model": "",
        "openai_model": "",
        "default_model_id": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_explicit_catalog_is_authoritative_and_preserves_configured_fields():
    catalog = build_model_catalog(
        settings(
            model_catalog_json=(
                '[{"id":"fast","value":"provider-fast","provider":"gateway-a",'
                '"label":"Fast","description":"Low latency",'
                '"profile":{"max_input_tokens":32000}},'
                '{"id":"quality","value":"provider-quality","provider":"gateway-b",'
                '"label":"Quality","description":"High quality",'
                '"max_input_tokens":64000}]'
            ),
            claude_agent_model="runtime-default",
            default_model_id="quality",
        )
    )

    assert catalog == {
        "models": [
            {
                "id": "fast",
                "value": "provider-fast",
                "provider": "gateway-a",
                "label": "Fast",
                "description": "Low latency",
                "profile": {"max_input_tokens": 32000},
            },
            {
                "id": "quality",
                "value": "provider-quality",
                "provider": "gateway-b",
                "label": "Quality",
                "description": "High quality",
                "profile": {"max_input_tokens": 64000},
            },
        ],
        "count": 2,
        "enabled_count": 2,
        "default_model_id": "quality",
    }


def test_explicit_catalog_normalizes_blank_value_and_provider_without_environment_description():
    catalog = build_model_catalog(
        settings(
            model_catalog_json=(
                '[{"id":"configured-model","value":"   ","provider":"   "}]'
            ),
            llm_gateway_provider="configured-gateway",
        )
    )

    assert catalog["models"] == [
        {
            "id": "configured-model",
            "value": "configured-model",
            "provider": "configured-gateway",
            "label": "configured-model",
            "description": "",
            "profile": {"max_input_tokens": DEFAULT_MAX_INPUT_TOKENS},
        }
    ]


@pytest.mark.parametrize(
    ("runtime_models", "expected_model"),
    [
        (
            {
                "claude_agent_model": "claude-runtime",
                "anthropic_model": "anthropic-runtime",
                "openai_model": "openai-runtime",
            },
            "claude-runtime",
        ),
        (
            {
                "claude_agent_model": "   ",
                "anthropic_model": "anthropic-runtime",
                "openai_model": "openai-runtime",
            },
            "anthropic-runtime",
        ),
        (
            {
                "claude_agent_model": "",
                "anthropic_model": "anthropic-runtime",
                "openai_model": "openai-runtime",
            },
            "anthropic-runtime",
        ),
        (
            {
                "claude_agent_model": "",
                "anthropic_model": "",
                "openai_model": "openai-runtime",
            },
            "openai-runtime",
        ),
    ],
)
def test_empty_catalog_derives_one_model_from_runtime_configuration(
    runtime_models,
    expected_model,
):
    catalog = build_model_catalog(
        settings(
            **runtime_models,
            llm_gateway_provider="runtime-gateway",
        )
    )

    assert catalog["default_model_id"] == expected_model
    assert catalog["models"] == [
        {
            "id": expected_model,
            "value": expected_model,
            "provider": "runtime-gateway",
            "label": expected_model,
            "description": "",
            "profile": {"max_input_tokens": DEFAULT_MAX_INPUT_TOKENS},
        }
    ]


def test_missing_catalog_and_runtime_model_fails_closed():
    with pytest.raises(ValueError, match=MODEL_CATALOG_NOT_CONFIGURED):
        build_model_catalog(settings())


@pytest.mark.parametrize("raw_catalog", ["[]", '[{"label":"missing-id"}]'])
def test_explicit_catalog_without_usable_models_fails_closed(raw_catalog):
    with pytest.raises(ValueError, match=MODEL_CATALOG_NOT_CONFIGURED):
        build_model_catalog(
            settings(
                model_catalog_json=raw_catalog,
                claude_agent_model="runtime-fallback-must-not-override-explicit-catalog",
            )
        )


def test_duplicate_catalog_ids_keep_first_configured_entry():
    catalog = build_model_catalog(
        settings(
            model_catalog_json=(
                '[{"id":"duplicate","value":"first"},'
                '{"id":"duplicate","value":"second"}]'
            )
        )
    )

    assert [model["value"] for model in catalog["models"]] == ["first"]


def test_malformed_catalog_json_fails_closed():
    with pytest.raises(ValueError):
        build_model_catalog(settings(model_catalog_json="{not-json"))


def test_model_selection_resolves_configured_value_and_rejects_unknown_id():
    configured = settings(
        model_catalog_json='[{"id":"public-id","value":"provider-model"}]'
    )

    assert resolve_model_selection(None, configured) is None
    assert resolve_model_selection("public-id", configured) == {
        "id": "public-id",
        "value": "provider-model",
    }
    with pytest.raises(ValueError, match="model_id_not_available"):
        resolve_model_selection("missing-id", configured)
