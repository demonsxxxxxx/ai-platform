"""Framework-neutral administrator projection contract for Run diagnostics."""

from typing import Any, Literal, NotRequired, TypedDict


class AdminRunDiagnosticRun(TypedDict):
    run_id: str
    session_id: str | None
    user_id: str | None
    workspace_id: str
    status: str
    trace_id: str | None
    created_at: Any | None
    queued_at: Any | None
    started_at: Any | None
    finished_at: Any | None
    error_code: str | None


class AdminRunDiagnosticObservation(TypedDict):
    observation_id: str | None
    attempt_id: str | None
    lease_id: str | None
    request_id: str | None
    callback_id: str | None
    kind: Literal["failure", "handling"]
    source: str | None
    stage: str | None
    error_code: str | None
    exception_type: str | None
    message: str | None
    stack: str | None
    received_at: Any | None


class AdminRunDiagnosticLoss(TypedDict):
    field: str
    reason: str
    original_bytes: NotRequired[int]
    retained_bytes: NotRequired[int]
    original: NotRequired[int]
    retained: NotRequired[int]
    count: NotRequired[int]


class AdminRunDiagnosticAttempt(TypedDict):
    attempt_id: str
    ordinal: int
    status: str
    owner_kind: str
    started_at: Any | None
    finished_at: Any | None
    terminal_reason: str | None
    error_code: str | None


class AdminRunDiagnosticSdk(TypedDict, total=False):
    result_subtype: str
    stop_reason: str
    terminal_reason: str
    exception_type: str
    exception_message: str
    exception_traceback: str
    errors: Any


class AdminRunDiagnosticToolEvidence(TypedDict):
    tool_name: str
    invocation_id: NotRequired[str]
    state: NotRequired[str]
    last_stage: NotRequired[str]
    capability_kind: NotRequired[str]
    reason: NotRequired[str]


class AdminRunDiagnosticProtocolField(TypedDict):
    present: bool
    type: str
    bytes: NotRequired[int]
    non_empty: NotRequired[bool]
    items: NotRequired[int]


class AdminRunDiagnosticProtocolValidation(TypedDict):
    location: str
    type: str
    message: str


class AdminRunDiagnosticProtocolReported(TypedDict):
    task_status: str | None
    terminal_status: str | None
    run_id_matches: bool | None
    fields: dict[str, AdminRunDiagnosticProtocolField]
    additional_field_count: int


class AdminRunDiagnosticProtocolCanonical(TypedDict):
    status: str
    error_code: str
    message_non_empty: bool
    answer_receipt_present: bool
    structured_error_present: bool


class AdminRunDiagnosticExecutorProtocol(TypedDict):
    reported: AdminRunDiagnosticProtocolReported
    validation: list[AdminRunDiagnosticProtocolValidation]
    validation_omitted_count: int
    canonical: AdminRunDiagnosticProtocolCanonical


class AdminRunDiagnosticDetails(TypedDict):
    schema_version: str | None
    sdk: AdminRunDiagnosticSdk
    tool_lifecycles: list[AdminRunDiagnosticToolEvidence]
    tool_calls: list[AdminRunDiagnosticToolEvidence]
    tool_policy_denials: list[AdminRunDiagnosticToolEvidence]
    executor_protocol: AdminRunDiagnosticExecutorProtocol | None


class AdminRunDiagnosticCounts(TypedDict):
    retained_observations: int
    omitted_observations: int


class AdminRunDiagnosticsResponse(TypedDict):
    schema_version: Literal["ai-platform.run-diagnostics.v1"]
    diagnostic_id: str | None
    revision: int
    coverage: Literal[
        "full",
        "partial",
        "not_collected",
        "legacy_record",
        "unsupported_schema",
        "transport_unavailable",
    ]
    run: AdminRunDiagnosticRun
    root: AdminRunDiagnosticObservation | None
    handling: list[AdminRunDiagnosticObservation]
    losses: list[AdminRunDiagnosticLoss]
    attempts: list[AdminRunDiagnosticAttempt]
    details: AdminRunDiagnosticDetails
    versions: dict[str, str | None]
    counts: AdminRunDiagnosticCounts
