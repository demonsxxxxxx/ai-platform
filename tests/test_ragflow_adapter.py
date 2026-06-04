from types import SimpleNamespace

import pytest

from app.executors.base import RunPayload
from app.executors.ragflow import (
    RagflowAdapter,
    build_answer,
    extract_intent_terms,
    extract_required_terms,
    extract_ragflow_chunks,
    resolve_ragflow_question,
)


RELEASE_DECISION_SCHEMA_VERSION = "ai-platform.skill-release-decision.v1"


def release_decision(version: str) -> dict:
    return {
        "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
        "policy_active": False,
        "selected_version": version,
        "selected_track": "manifest_pin",
    }


def run_payload(*, input_payload: dict, skill_id: str = "ragflow-knowledge-search") -> RunPayload:
    version = f"hash-{skill_id}"
    return RunPayload(
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="ses-a",
        run_id="run-a",
        agent_id="sop-assistant",
        skill_id=skill_id,
        file_ids=[],
        input=input_payload,
        skill_version=version,
        release_decision=release_decision(version),
        skill_manifests=[{"skill_id": skill_id, "content_hash": version}],
    )


def test_resolve_ragflow_question_accepts_question_message_or_prompt():
    assert resolve_ragflow_question({"question": "SOP 怎么查？"}) == "SOP 怎么查？"
    assert resolve_ragflow_question({"message": "偏差怎么处理？"}) == "偏差怎么处理？"
    assert resolve_ragflow_question({"prompt": "培训记录保存多久？"}) == "培训记录保存多久？"


def test_extract_ragflow_chunks_normalizes_retrieval_response():
    payload = {
        "data": {
            "chunks": [
                {
                    "id": "chunk-a",
                    "docnm_kwd": "QA-SOP.docx",
                    "kb_id": "dataset-a",
                    "similarity": 0.91,
                    "content_with_weight": "第一条 SOP 内容",
                }
            ]
        }
    }

    chunks = extract_ragflow_chunks(payload)

    assert chunks == [
        {
            "document_name": "QA-SOP.docx",
            "document_id": None,
            "dataset_id": "dataset-a",
            "chunk_id": "chunk-a",
            "similarity": 0.91,
            "content": "第一条 SOP 内容",
        }
    ]


def test_extract_required_terms_uses_explicit_system_names():
    assert extract_required_terms("lims账号如何申请") == ["lims"]
    assert extract_required_terms("SOP 怎么查？") == []


def test_extract_intent_terms_uses_account_application_words():
    assert extract_intent_terms("LIMS账号如何申请") == ["账号", "申请"]


def test_build_answer_does_not_claim_low_relevance_candidates_as_answer():
    chunks = [
        {
            "document_name": "ERP账号申请.pdf",
            "content": "PM运营在 OA 流程中提交 IT 服务工单，申请开通 ERP 账号。",
            "similarity": 0.47,
        }
    ]

    answer = build_answer("LIMS账号如何申请", chunks)

    assert "未检索到包含“LIMS”的明确依据" in answer
    assert "低相关候选片段" in answer
    assert "根据知识库检索结果，相关内容如下" not in answer


def test_build_answer_requires_system_and_intent_evidence_together():
    chunks = [
        {
            "document_name": "IT业务连续性计划.pdf",
            "content": "关键业务系统包括 ERP、OA、DMS、TMS、LIMS 等。",
            "similarity": 0.6,
        }
    ]

    answer = build_answer("LIMS账号如何申请", chunks)

    assert "未检索到同时包含“LIMS”和“账号、申请”的明确依据" in answer
    assert "根据知识库检索结果，相关内容如下" not in answer


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, *, response):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json=None, headers=None):
        self.requests.append((url, json, headers))
        return self.response


@pytest.mark.asyncio
async def test_ragflow_adapter_returns_answer_with_references(monkeypatch):
    response_payload = {
        "code": 0,
        "data": {
            "chunks": [
                {
                    "chunk_id": "chunk-a",
                    "document_name": "QA-SOP.docx",
                    "dataset_id": "dataset-a",
                    "similarity": 0.88,
                    "content": "偏差处理应按 SOP 记录、评估并关闭。",
                }
            ]
        },
    }
    fake_client = FakeAsyncClient(response=FakeResponse(response_payload))

    monkeypatch.setattr("app.executors.ragflow.httpx.AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(
        "app.executors.ragflow.get_settings",
        lambda: SimpleNamespace(
            ragflow_api_url="http://ragflow.local",
            ragflow_api_key="secret",
            ragflow_default_dataset_id="dataset-a",
            ragflow_timeout_seconds=30.0,
            ragflow_top_k=3,
            ragflow_similarity_threshold=0.2,
        ),
    )

    adapter = RagflowAdapter()
    result = await adapter.submit_run(run_payload(input_payload={"question": "偏差怎么处理？"}))

    assert result.status == "succeeded"
    assert result.result["answer"]
    assert result.result["references"][0]["document_name"] == "QA-SOP.docx"
    assert "dataset_ids" not in result.result
    assert "dataset_id" not in result.result["references"][0]
    assert "chunk_id" not in result.result["references"][0]
    assert "document_id" not in result.result["references"][0]
    assert result.executor_payload["dataset_ids"] == ["dataset-a"]
    assert fake_client.requests[0][0] == "http://ragflow.local/api/v1/retrieval"
    assert fake_client.requests[0][1]["dataset_ids"] == ["dataset-a"]
    assert fake_client.requests[0][2]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_ragflow_adapter_fails_without_question(monkeypatch):
    monkeypatch.setattr(
        "app.executors.ragflow.get_settings",
        lambda: SimpleNamespace(
            ragflow_api_url="http://ragflow.local",
            ragflow_api_key="secret",
            ragflow_default_dataset_id="dataset-a",
            ragflow_timeout_seconds=30.0,
            ragflow_top_k=3,
            ragflow_similarity_threshold=0.2,
        ),
    )

    result = await RagflowAdapter().submit_run(run_payload(input_payload={}))

    assert result.status == "failed"
    assert result.result["error_code"] == "ragflow_missing_question"


@pytest.mark.asyncio
async def test_ragflow_api_error_keeps_raw_payload_internal_only(monkeypatch):
    response_payload = {
        "code": 1001,
        "message": "dataset dataset-secret chunk chunk-secret unavailable token=hidden-token",
        "data": {
            "dataset_id": "dataset-secret",
            "chunk_id": "chunk-secret",
            "token": "hidden-token",
            "apiKey": "hidden-api-key",
            "access_token": "hidden-access-token",
            "authorization_header": "Bearer hidden-auth",
        },
    }
    fake_client = FakeAsyncClient(response=FakeResponse(response_payload))

    monkeypatch.setattr("app.executors.ragflow.httpx.AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(
        "app.executors.ragflow.get_settings",
        lambda: SimpleNamespace(
            ragflow_api_url="http://ragflow.local",
            ragflow_api_key="secret",
            ragflow_default_dataset_id="dataset-secret",
            ragflow_timeout_seconds=30.0,
            ragflow_top_k=3,
            ragflow_similarity_threshold=0.2,
        ),
    )

    result = await RagflowAdapter().submit_run(run_payload(input_payload={"question": "SOP 怎么查？"}))

    assert result.status == "failed"
    assert result.result == {
        "message": "RAGFlow retrieval failed.",
        "error_code": "ragflow_api_error",
        "retryable": False,
    }
    assert "ragflow_payload" in result.executor_payload
    assert result.executor_payload["ragflow_payload"]["message"] == (
        "dataset dataset-secret chunk chunk-secret unavailable [redacted-secret]"
    )
    assert "token" not in str(result.executor_payload["ragflow_payload"])
    assert "apiKey" not in str(result.executor_payload["ragflow_payload"])
    assert "access_token" not in str(result.executor_payload["ragflow_payload"])
    assert "authorization_header" not in str(result.executor_payload["ragflow_payload"])
    assert "hidden-token" not in str(result)
    assert "hidden-api-key" not in str(result)
    assert "hidden-access-token" not in str(result)
    assert "hidden-auth" not in str(result)
    assert "dataset-secret" not in str(result.result)
    assert "chunk-secret" not in str(result.result)
