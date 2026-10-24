ARTIFACT_PREVIEW_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)


def artifact_preview_allowed(content_type: object) -> bool:
    """Return whether an artifact MIME type may be served by the preview route."""
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    return normalized in ARTIFACT_PREVIEW_ALLOWED_CONTENT_TYPES


def artifact_preview_url(artifact_id: str) -> str:
    """Build the public platform preview URL for an artifact."""
    return f"/api/ai/artifacts/{artifact_id}/preview"
