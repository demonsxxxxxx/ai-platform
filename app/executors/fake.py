from app.executors.base import ArtifactManifest, ExecutorEventSink, ExecutorResult, RunPayload


class FakeSuccessAdapter:
    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        return ExecutorResult(
            status="succeeded",
            adapter_version="fake-adapter/1",
            executor_type="fake",
            executor_version="fake-executor/1",
            capabilities={
                "skills": True,
                "mcp": False,
                "streaming": False,
            },
            result={
                "message": f"fake run completed for {payload.run_id}",
                "skill_id": payload.skill_id,
            },
            artifacts=[
                ArtifactManifest(
                    artifact_type="test_json",
                    label="Test JSON",
                    content_type="application/json",
                    storage_key=f"tenants/{payload.tenant_id}/runs/{payload.run_id}/fake-result.json",
                    size_bytes=2,
                    manifest={"source": "fake"},
                )
            ],
        )


class FakeFailureAdapter:
    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            adapter_version="fake-adapter/1",
            executor_type="fake",
            executor_version="fake-executor/1",
            capabilities={
                "skills": True,
                "mcp": False,
                "streaming": False,
            },
            result={
                "message": f"fake run failed for {payload.run_id}",
                "error_code": "fake_failure",
            },
        )
