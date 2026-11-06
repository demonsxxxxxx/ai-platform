"""Compose administrator read evidence without exposing concrete adapters across contexts."""
from app.auth import is_ai_admin, require_principal
from app.routes import admin_runs
from app.runs.transport.admin_inspection import build_admin_inspection_router
from app.files.application.admin_run_inputs import configure_admin_run_input_reader
from app.files.infrastructure.admin_run_inputs_postgres import read_admin_run_input_files
from app.streaming.application.admin_evidence import configure_admin_event_evidence
from app.streaming.infrastructure.admin_detail_postgres import read_admin_detail_events
from app.streaming.infrastructure.private_model_evidence import append_private_model_call, append_run_trigger


def build_admin_run_inspection_router():
    return build_admin_inspection_router(principal_dependency=require_principal, is_admin=is_ai_admin,
        transaction=lambda: admin_runs.transaction(), service_for=lambda request: admin_runs._require_run_diagnostics_service(request))


def configure_admin_run_evidence() -> None:
    configure_admin_run_input_reader(read_admin_run_input_files)
    configure_admin_event_evidence(read=read_admin_detail_events, append=append_private_model_call, trigger=append_run_trigger)
