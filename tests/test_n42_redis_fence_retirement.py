from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import queue
from app.bootstrap import execution_stale_run_reconciliation as stale_reconciliation_bootstrap
import app.worker_main as worker_main
from app.settings import Settings


def test_queue_module_has_no_redis_business_reconciliation_fence_surface():
    retired_symbols = (
        "RECONCILIATION_FENCE_PREFIX",
        "RunReconciliationFence",
        "reconciliation_fence_key",
        "acquire_run_reconciliation_fence",
        "renew_run_reconciliation_fence",
        "release_run_reconciliation_fence",
    )
    assert all(not hasattr(queue, name) for name in retired_symbols)

    queue_scripts = [
        getattr(queue, name)
        for name in dir(queue)
        if name.endswith("_SCRIPT") and isinstance(getattr(queue, name), str)
    ]
    assert all("reconciliation-fence" not in script for script in queue_scripts)
    assert all("reconciliation_fenced" not in script for script in queue_scripts)
    assert not hasattr(worker_main, "_reconcile_stale_runs_with_redis_fence")
    assert not hasattr(Settings(_env_file=None), "stale_run_reconciliation_fence_ttl_seconds")
    assert not hasattr(Settings(_env_file=None), "stale_run_reconciliation_postgres_claims_enabled")


@pytest.mark.asyncio
async def test_stale_reconciliation_bootstrap_dispatches_with_schema_current(monkeypatch):
    calls = []

    async def require_current_schema():
        calls.append("schema_current")
        return {"ready": True}

    class Service:
        async def reconcile_postgres_claims(self, **kwargs):
            calls.append(("claims", kwargs))
            return []

    def build_service(**kwargs):
        assert kwargs["lifecycle"] is lifecycle
        assert kwargs["attempt_lifecycle"] is attempt_lifecycle
        assert kwargs["v4_capabilities"] is capabilities
        calls.append("service_built")
        return Service()

    lifecycle = object()
    attempt_lifecycle = object()
    capabilities = object()
    monkeypatch.setattr(
        stale_reconciliation_bootstrap, "require_schema_current", require_current_schema
    )
    monkeypatch.setattr(
        stale_reconciliation_bootstrap,
        "build_stale_run_reconciliation_service",
        build_service,
    )
    settings = SimpleNamespace(
        stale_run_reconciliation_limit=8,
        stale_run_reconciliation_seconds=1200,
        stale_run_reconciliation_claim_ttl_seconds=480,
    )

    results = await stale_reconciliation_bootstrap.reconcile_worker_stale_runs_with_postgres_claims(
        settings,
        lifecycle=lifecycle,
        attempt_lifecycle=attempt_lifecycle,
        v4_capabilities=capabilities,
        worker_id="worker-n42",
        cancel_requested_after_seconds=5,
    )

    assert results == []
    assert calls == [
        "schema_current",
        "service_built",
        (
            "claims",
            {
                "worker_id": "worker-n42",
                "limit": 8,
                "stale_after_seconds": 1200,
                "cancel_requested_after_seconds": 5,
                "claim_ttl_seconds": 480,
            },
        ),
    ]
