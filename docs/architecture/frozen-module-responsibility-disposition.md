# Frozen Module Responsibility Disposition

This document defines the source ownership, compatibility, and retirement
contract for the six frozen backend aggregates. It does not claim that a
candidate is merged, packaged, deployed, or externally accepted.

## Owner map

| Frozen source | Canonical responsibility moved | Disposition |
| --- | --- | --- |
| `app/models.py` | Conversation session and message-history HTTP contracts in `app/conversations/transport/message_history_contracts.py` | `app.models` retains exact identity exports through `app/compat/conversation_message_compat.py` for existing imports. |
| `app/repositories.py` | Context-specific PostgreSQL adapters | Movement is blocked unless an exact `migration_bridges` authority grant names the source, target, alias, and symbols. |
| `app/executors/claude_agent_worker.py` | File-workflow selection, dependency expansion, resume checkpoints, and non-execution step projection in `app/execution/application/file_workflow.py` | Application logic is exposed through `app.execution.api`; provider-specific orchestration remains in the adapter. |
| `app/routes/chat.py` | Message cursor encoding and public message projection in `app/conversations/application/message_history.py` | Application logic is exposed through `app.conversations.api`; HTTP exception mapping remains in transport. |
| `app/runtime/sandbox/container_provider.py` | Secure OpenSandbox workspace manifest, stable read, and no-follow descriptor traversal in `app/sandbox/infrastructure/workspace_transfer.py` | Movement requires one exact architecture-policy migration bridge; provider lifecycle and remote SDK calls remain in the legacy provider. |
| `app/worker.py` | Executor observability, artifact-manifest sanitization, Skill result projection, and dependency projection in `app/execution/application/worker_result_projection.py` | Application logic is exposed through `app.execution.api`; durable transaction and terminalization orchestration remain in the worker. |

## Preserved boundaries

The move does not change transaction or lock scope, queue payload identity,
Run/Attempt ownership, executor selection, remote workspace paths, HTTP response
models, cursor format, public redaction, or terminalization behavior. Application
modules accept framework-neutral protocols or injected policy functions instead
of importing legacy root implementations. Legacy callers reach application logic
only through the owning `api.py` boundary.

## Compatibility and retirement

`app.models` remains an active import surface. Its six conversation-history
exports are the same class objects as the Conversations transport owner. Remove
the compatibility module only after repository and supported external import
inventory is empty and the identity contract test can be deleted with it.

The OpenSandbox bridge is narrower than a general facade: it permits only the
symbols listed in `architecture-policy.json`, and the frozen source contains only
the declared module import and exact identity aliases for those symbols. Retire
it using the architecture contract's two changes: remove authority after the
removal condition is proven, then remove aliases under the next authority.

The compatibility audit found the following active surfaces and deliberately
retains them:

- LambChat `/api/sessions*` routes and frontend callers;
- `app.agent_apps` and `app.context` lazy exports;
- browser/config readers for `uploadLimits`, file `url`, and login `username`
  aliases; and
- existing repository migration bridges, including Agent Profile persistence.

Retiring the Agent Profile bridge or any remaining `app.repositories` symbol is
an authority-only follow-up, not part of this implementation candidate.

## Superseded-path inventory

The moved local definitions are removed from the five changed frozen sources.
Their tests either exercise the canonical owner directly or patch the declared
legacy alias when validating legacy orchestration. No parallel workflow,
workspace-transfer implementation, message projector, or worker result projector
remains active. `app/repositories.py` has no superseded implementation in this
change because no additional repository migration was authorized.

## Delivery order

1. Land the exact Sandbox `migration_bridges` policy entry as an authority-only
   change.
2. Evaluate the implementation candidate against that fixed authority and base.
3. Keep compatibility until its named consumer inventory and removal condition
   are satisfied; do not combine bridge retirement with implementation movement.
