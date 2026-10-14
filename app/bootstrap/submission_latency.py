"""Wire chat submission authority into its browser observation transport."""
from fastapi import APIRouter

from app.auth import require_principal
from app.conversations.transport.submission_latency import build_submission_latency_router
from app.routes import chat


def build_chat_submission_latency_router() -> APIRouter:
    return build_submission_latency_router(
        resolve_submission=chat._resolve_chat_submission,
        transaction_factory=chat.transaction,
        principal_dependency=require_principal,
    )
