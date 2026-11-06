"""Run-bound administrator artifact access; metadata never probes object storage."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.artifacts.domain.preview import artifact_preview_allowed


class AdminArtifactAccessError(ValueError):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class AdminRunArtifactAccess:
    transaction: Callable[..., Any]
    read_binding: Callable[..., Any]
    audit: Callable[..., Any]

    async def resolve(self, *, tenant_id: str, user_id: str, run_id: str,
                      artifact_id: str, action: str) -> dict[str, Any]:
        async with self.transaction() as conn:
            artifact = await self.read_binding(conn, tenant_id=tenant_id, run_id=run_id, artifact_id=artifact_id)
            if artifact is None:
                raise AdminArtifactAccessError("artifact_not_found", 404)
            expires = artifact.get("expires_at")
            availability = "expired" if isinstance(expires, datetime) and expires <= datetime.now(timezone.utc) else "unverified"
            if artifact.get("lifecycle_state") != "active":
                availability = "missing"
            if action == "access":
                return {"artifact_id": artifact_id, "run_id": run_id, "access": "allowed",
                        "availability": availability, "preview_allowed": artifact_preview_allowed(artifact.get("content_type"))}
            if availability != "unverified":
                raise AdminArtifactAccessError("artifact_expired" if availability == "expired" else "artifact_not_found", 410 if availability == "expired" else 404)
            if action == "preview" and not artifact_preview_allowed(artifact.get("content_type")):
                raise AdminArtifactAccessError("artifact_preview_not_allowed", 415)
            # A failed audit aborts the transaction and no content is returned.
            await self.audit(conn, tenant_id=tenant_id, user_id=user_id,
                             action=f"admin_run_artifact_{action}", target_type="artifact", target_id=artifact_id,
                             trace_id=artifact.get("trace_id"), payload_json={"run_id": run_id, "artifact_id": artifact_id,
                             "admin_user_id": user_id, "target_user_id": artifact.get("target_user_id")})
            return artifact
