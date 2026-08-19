import json
from types import SimpleNamespace

import pytest

from app.model_catalog import (
    DEFAULT_MAX_INPUT_TOKENS,
    MODEL_CATALOG_NOT_CONFIGURED,
    build_model_catalog,
    resolve_model_selection,
)
from app.platform.model_upstream import fetch_upstream_openai_models


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


@pytest.mark.parametrize(
    "item",
    [
        {"id": 123},
        {"id": "configured-model", "value": 123},
        {"id": "configured-model", "provider": {"name": "gateway"}},
        {"id": "configured-model", "label": ["label"]},
        {"id": "configured-model", "description": True},
        {"id": "configured-model", "profile": []},
        {"id": "configured-model", "max_input_tokens": True},
        {"id": "configured-model", "max_input_tokens": 1.5},
        {"id": "configured-model", "max_input_tokens": "32000"},
    ],
)
def test_explicit_catalog_rejects_non_contract_field_types(item):
    with pytest.raises(ValueError, match=MODEL_CATALOG_NOT_CONFIGURED):
        build_model_catalog(
            settings(
                model_catalog_json=json.dumps([item]),
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


@pytest.mark.parametrize("invalid_model_id", [True, 123, 1.5, [], {}])
def test_model_selection_rejects_non_string_ids_without_coercion(invalid_model_id):
    configured = settings(
        model_catalog_json=(
            '[{"id":"True"},{"id":"123"},{"id":"1.5"}]'
        )
    )

    with pytest.raises(ValueError, match="model_id_not_available"):
        resolve_model_selection(invalid_model_id, configured)


@pytest.mark.asyncio
async def test_fetch_upstream_openai_models_returns_mapped_options(monkeypatch):
    import httpx

    async def fake_get(self, url, **kwargs):
        assert url.endswith("/v1/models")
        return httpx.Response(
            200,
            json={"data": [{"id": "model-a"}, {"id": "model-b"}]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    models = await fetch_upstream_openai_models(
        SimpleNamespace(
            openai_base_url="http://gateway.test",
            openai_api_key="secret",
            llm_gateway_provider="openai_compatible",
        )
    )
    assert [m["id"] for m in models] == ["model-a", "model-b"]
    assert all(m["provider"] == "openai_compatible" for m in models)
    assert all(m["value"] == m["id"] for m in models)


@pytest.mark.asyncio
async def test_fetch_upstream_openai_models_falls_back_empty_on_failure(monkeypatch):
    import httpx

    from app.platform import model_upstream as model_upstream_module

    model_upstream_module._upstream_model_cache.update(
        {"fetched_at": 0.0, "models": [], "error": None}
    )

    async def fake_get(self, url, **kwargs):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    models = await fetch_upstream_openai_models(
        SimpleNamespace(
            openai_base_url="http://gateway.test",
            openai_api_key="",
            llm_gateway_provider="openai_compatible",
        )
    )
    assert models == []


def test_resolve_model_selection_accepts_upstream_ids(monkeypatch):
    selection = resolve_model_selection(
        "claude-sonnet-5",
        settings(),
        upstream_ids={"claude-sonnet-5", "deepseek-v4"},
    )
    assert selection == {"id": "claude-sonnet-5", "value": "claude-sonnet-5"}
    with pytest.raises(ValueError):
        resolve_model_selection("not-in-upstream", settings(), upstream_ids=set())
