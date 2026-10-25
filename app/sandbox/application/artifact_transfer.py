"""Executor-private streaming artifact download and attempt-local staging."""

import hashlib
import json
import re

from collections.abc import Callable
from typing import Any

from app.context.api import (
    ARTIFACT_STAGE_MAX_BYTES,
    ARTIFACT_TRANSFER_CHUNK_BYTES,
    ARTIFACT_TRANSFER_HEADER,
    ArtifactTransferError,
    artifact_workspace_parts,
    ARTIFACT_TRANSFER_SAFE_ERRORS,
)
from app.context.api import ArtifactWorkspace, artifact_stage_result


class ArtifactStreamStager:
    def __init__(self, *, http_client_factory: Callable[..., Any]):
        self._http_client_factory = http_client_factory
        self.workspace = ArtifactWorkspace()

    async def stage(
        self, *, callback_url, callback_token, payload, workspace_root, timeout_seconds
    ):
        arguments = payload["arguments"]
        if set(arguments) - {"artifact_id", "max_bytes"}:
            raise ArtifactTransferError("context_retrieval_parameters_invalid")
        artifact_id = str(arguments.get("artifact_id") or "")
        cap = min(
            ARTIFACT_STAGE_MAX_BYTES,
            max(1, int(arguments.get("max_bytes", ARTIFACT_STAGE_MAX_BYTES))),
        )
        async with self._http_client_factory(timeout=timeout_seconds) as client:
            async with client.stream(
                "POST",
                callback_url,
                json=payload,
                headers={
                    "X-AI-Platform-Callback-Token": callback_token,
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code in {401, 403, 409, 413, 422}:
                    error_body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        error_body.extend(chunk)
                        if len(error_body) > 4096:
                            raise RuntimeError("context_retrieval_callback_invalid")
                    try:
                        reason = json.loads(error_body).get("detail")
                    except (ValueError, AttributeError):
                        reason = None
                    raise ArtifactTransferError(
                        reason
                        if isinstance(reason, str)
                        and reason in ARTIFACT_TRANSFER_SAFE_ERRORS
                        else "context_scope_denied"
                    )
                if response.status_code != 200:
                    raise RuntimeError("context_retrieval_callback_failed")
                try:
                    metadata = json.loads(response.headers[ARTIFACT_TRANSFER_HEADER])
                    expected = int(response.headers["content-length"])
                    digest = metadata["sha256"]
                    if expected > cap:
                        raise ArtifactTransferError("context_artifact_too_large")
                    if (
                        response.headers.get("content-encoding", "identity")
                        != "identity"
                        or response.headers.get("content-type")
                        != "application/octet-stream"
                        or metadata["artifact_id"] != artifact_id
                        or type(metadata["size_bytes"]) is not int
                        or metadata["size_bytes"] != expected
                        or expected < 0
                        or expected > cap
                        or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    ):
                        raise ValueError()
                    artifact_workspace_parts(artifact_id, metadata["name"])
                    metadata["max_bytes"] = cap
                except ArtifactTransferError:
                    raise
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("context_retrieval_callback_invalid") from exc
                async with self.workspace.lock(
                    workspace_root, artifact_id, metadata["name"]
                ):
                    with self.workspace.target(
                        workspace_root,
                        artifact_id,
                        metadata["name"],
                        source_identity=(
                            digest,
                            expected,
                            metadata.get("source_run_id", ""),
                        ),
                    ) as target:
                        reused = target.reuse()
                        if reused is not None:
                            return artifact_stage_result(
                                metadata, bytes_staged=reused, reused=True
                            )
                        size = 0
                        actual = hashlib.sha256()
                        with target.output() as output:
                            async for chunk in response.aiter_raw(
                                chunk_size=ARTIFACT_TRANSFER_CHUNK_BYTES
                            ):
                                size += len(chunk)
                                if size > expected or size > cap:
                                    raise ArtifactTransferError(
                                        "context_artifact_identity_mismatch"
                                    )
                                actual.update(chunk)
                                output.write(chunk)
                            if size != expected or actual.hexdigest() != digest:
                                raise ArtifactTransferError(
                                    "context_artifact_identity_mismatch"
                                )
                        return artifact_stage_result(
                            metadata, bytes_staged=size, reused=False
                        )
