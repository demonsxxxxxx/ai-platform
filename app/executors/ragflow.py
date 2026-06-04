import re
from typing import Any

import httpx

from app.executors.base import ExecutorEventSink, ExecutorResult, RunPayload
from app.settings import get_settings


class RagflowAdapter:
    adapter_version = "ragflow-adapter/1"
    executor_type = "ragflow"
    executor_version = "ragflow-retrieval-http"
    capabilities = {
        "artifacts": False,
        "streaming": False,
        "tools": True,
    }

    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        question = resolve_ragflow_question(payload.input)
        if not question:
            return self._failure_result(
                "ragflow_missing_question",
                "RAGFlow question is required. Use input.question, input.message, or input.prompt.",
            )

        settings = get_settings()
        base_url = settings.ragflow_api_url.rstrip("/")
        api_key = settings.ragflow_api_key.strip()
        if not base_url or not api_key:
            return self._failure_result(
                "ragflow_not_configured",
                "RAGFlow API URL and API key must be configured on the server.",
            )

        dataset_ids = resolve_dataset_ids(payload.input, settings.ragflow_default_dataset_id)
        body: dict[str, Any] = {
            "question": question,
            "top_k": int(payload.input.get("top_k") or settings.ragflow_top_k),
            "page": 1,
            "page_size": int(payload.input.get("page_size") or settings.ragflow_top_k),
            "size": int(payload.input.get("page_size") or settings.ragflow_top_k),
            "similarity_threshold": float(
                payload.input.get("similarity_threshold") or settings.ragflow_similarity_threshold
            ),
            "highlight": False,
        }
        if dataset_ids:
            body["dataset_ids"] = dataset_ids

        try:
            async with httpx.AsyncClient(timeout=settings.ragflow_timeout_seconds) as client:
                response = await client.post(
                    f"{base_url}/api/v1/retrieval",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                raw_payload = response.json()
        except Exception as exc:
            return self._failure_result("ragflow_http_error", "RAGFlow retrieval failed.", retryable=True)

        api_code = raw_payload.get("code") if isinstance(raw_payload, dict) else None
        if api_code not in (0, None):
            return self._failure_result(
                "ragflow_api_error",
                "RAGFlow retrieval failed.",
                retryable=False,
                raw_payload=safe_payload(raw_payload),
            )

        chunks = extract_ragflow_chunks(raw_payload)
        public_references = public_reference_chunks(chunks)
        answer = build_answer(question, chunks)
        return ExecutorResult(
            status="succeeded",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self.capabilities,
            result={
                "message": answer,
                "answer": answer,
                "question": question,
                "references": public_references,
                "reference_count": len(chunks),
            },
            executor_payload={
                "endpoint": "/api/v1/retrieval",
                "dataset_ids": dataset_ids,
                "reference_ids": reference_identifiers(chunks),
            },
        )

    def _failure_result(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
        raw_payload: dict[str, Any] | None = None,
    ) -> ExecutorResult:
        result: dict[str, Any] = {
            "message": message,
            "error_code": error_code,
            "retryable": retryable,
        }
        executor_payload: dict[str, Any] = {}
        if raw_payload is not None:
            executor_payload["ragflow_payload"] = raw_payload
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self.capabilities,
            result=result,
            executor_payload=executor_payload,
        )


def resolve_ragflow_question(input_payload: dict[str, Any]) -> str:
    for key in ("question", "message", "prompt"):
        value = input_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_dataset_ids(input_payload: dict[str, Any], default_dataset_id: str) -> list[str]:
    raw = input_payload.get("dataset_ids")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    default_dataset_id = default_dataset_id.strip()
    return [default_dataset_id] if default_dataset_id else []


def extract_ragflow_chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        raw_chunks = data.get("chunks") or []
    elif isinstance(data, list):
        raw_chunks = data
    else:
        raw_chunks = []

    chunks: list[dict[str, Any]] = []
    for item in raw_chunks:
        if not isinstance(item, dict):
            continue
        chunks.append(
            {
                "document_name": item.get("document_keyword") or item.get("docnm_kwd") or item.get("document_name"),
                "document_id": item.get("document_id") or item.get("doc_id"),
                "dataset_id": item.get("dataset_id") or item.get("kb_id"),
                "chunk_id": item.get("chunk_id") or item.get("id"),
                "similarity": item.get("similarity"),
                "content": normalize_chunk_content(item),
            }
        )
    return chunks


def public_reference_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        references.append(
            {
                "index": index,
                "document_name": chunk.get("document_name"),
                "similarity": chunk.get("similarity"),
                "content": chunk.get("content") or "",
            }
        )
    return references


def reference_identifiers(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        references.append(
            {
                "index": index,
                "document_id": chunk.get("document_id"),
                "dataset_id": chunk.get("dataset_id"),
                "chunk_id": chunk.get("chunk_id"),
            }
        )
    return references


def normalize_chunk_content(chunk: dict[str, Any]) -> str:
    for key in ("content_with_weight", "content", "answer", "chunk"):
        value = chunk.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(item) for item in value)
    return ""


def build_answer(question: str, chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return f"知识库未检索到与“{question}”直接相关的内容。"
    required_terms = extract_required_terms(question)
    intent_terms = extract_intent_terms(question)
    evidence_chunks = filter_chunks_by_required_terms(chunks, required_terms, intent_terms)
    if required_terms and not evidence_chunks:
        terms = "、".join(term.upper() for term in required_terms)
        if intent_terms:
            intent_text = "、".join(intent_terms)
            message = f"知识库未检索到包含“{terms}”的明确依据，也未检索到同时包含“{terms}”和“{intent_text}”的明确依据。"
        else:
            message = f"知识库未检索到包含“{terms}”的明确依据。"
        lines = [f"{message}以下是系统返回的低相关候选片段，请不要直接作为结论："]
        for index, chunk in enumerate(chunks[:3], start=1):
            lines.append(format_chunk_line(index, chunk))
        return "\n".join(lines)

    lead = "根据知识库检索结果，相关内容如下："
    lines = [lead]
    for index, chunk in enumerate((evidence_chunks or chunks)[:3], start=1):
        lines.append(format_chunk_line(index, chunk))
    return "\n".join(lines)


def extract_required_terms(question: str) -> list[str]:
    terms = []
    for item in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", question or ""):
        normalized = item.lower()
        if normalized in {"sop"}:
            continue
        if normalized not in terms:
            terms.append(normalized)
    return terms


def extract_intent_terms(question: str) -> list[str]:
    terms = []
    for term in ("账号", "账户", "申请", "权限", "开通", "登录", "登陆"):
        if term in (question or "") and term not in terms:
            terms.append(term)
    return terms


def filter_chunks_by_required_terms(
    chunks: list[dict[str, Any]],
    required_terms: list[str],
    intent_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not required_terms:
        return chunks
    intent_terms = intent_terms or []
    matched = []
    for chunk in chunks:
        text = f"{chunk.get('document_name') or ''}\n{chunk.get('content') or ''}".lower()
        has_required_term = any(term in text for term in required_terms)
        has_intent_term = not intent_terms or any(term in text for term in intent_terms)
        if has_required_term and has_intent_term:
            matched.append(chunk)
    return matched


def format_chunk_line(index: int, chunk: dict[str, Any]) -> str:
    source = chunk.get("document_name") or "未知来源"
    content = " ".join(str(chunk.get("content") or "").split())
    if len(content) > 300:
        content = f"{content[:297]}..."
    return f"{index}. {source}: {content or '未返回正文片段'}"


def safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _safe_payload_value(payload) if isinstance(payload, dict) else {}


SECRET_KEY_FRAGMENTS = ("apikey", "accesstoken", "authorization", "secret", "token", "credential", "password")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|secret|token|credential|password)\s*[:=]\s*\S+"
)


def _is_secret_key(key: object) -> bool:
    normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
    return any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def _safe_payload_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe_payload_value(item)
            for key, item in value.items()
            if not _is_secret_key(key)
        }
    if isinstance(value, list):
        return [_safe_payload_value(item) for item in value]
    if isinstance(value, str):
        return SECRET_ASSIGNMENT_PATTERN.sub("[redacted-secret]", value)
    return value
