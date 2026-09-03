# ADR 0010: Sandbox-Owned Attachment Content Processing

Status: proposed; implementation included in the Issue #1144 cutover

Date: 2026-08-18

Decision ID: `ai-platform.sandbox-owned-attachment-content-processing.v1`

Owning issue: [#1144](https://github.com/demonsxxxxxx/ai-platform/issues/1144)

## Context

The platform currently treats document content extraction as an execution-admission
requirement. A selected Skill with attachments can trigger server-owned XLSX
preprocessing, which stages a workbook, parses cells, emits parser evidence,
and forwards a bounded JSON representation in an additional user message. The
legacy context-file path similarly extracts DOCX, PDF, and text content before
allowing a raw file to be staged.

This couples business-document support to platform context limits. A valid
workbook with more than 2,048 cells fails before the Agent starts, even though
the selected Skill owns the requested interpretation and can use a sandboxed
library to process the file. It also duplicates content-handling authority:
the platform reads attachment business data and the Skill may read the original
file again.

The product contract is instead simple: users supply business files to a
selected Agent/Skill, and that Skill processes the original bytes in its
isolated workspace. The platform owns authorization, safe delivery, and
resource isolation. It does not own business-document extraction.

## Decision

All run attachments use one raw-file contract. There is no preview, summary,
content-injection, type-specific, or lightweight-chat exception.

```text
upload/storage
  -> immutable run snapshot authorization
  -> bounded identity checks plus temporary XLSX package safety
  -> atomically materialized read-only inputs/
  -> file metadata manifest
  -> selected Agent/Skill reads original bytes in Sandbox
  -> bounded output/artifact collection
```

The platform passes a file only after it has established that the current run
is authorized for the exact snapshot-bound file and that it can be safely
written inside the run workspace. It does not extract paragraphs, tables,
spreadsheet cells, PDF text, or other business content.

## Atomic Responsibilities

### A1. Snapshot authorization

The broker resolves each requested `file_id` through the current immutable run
snapshot and requires tenant, workspace, user, session, and run identity to
match. A missing, stale, or cross-scope file fails closed before storage access.

Owner: context repository and broker.

Output: one authorized file record or a generic public availability failure.

### A2. Metadata and byte integrity

The platform validates a safe basename, declared non-negative byte count,
storage key presence, bounded storage read, and SHA-256 identity. It rejects
an object whose bytes do not match the snapshot-bound identity.

Owner: file continuity and storage boundary.

Output: immutable facts containing only file ID, basename, content type, byte
count, and digest. These facts are not document content.

### A3. Format-independent staging and temporary XLSX package safety

Attachment admission does not require a platform parser, recognized extension,
matching MIME type, or document magic signature. After A1-A2, DOC, DOCX, PDF, XLS,
PPT, PPTX, and other authorized files remain opaque original bytes. The selected
Skill may inspect those bytes only inside its Sandbox and under its existing tool
and network policy.

As a temporary resource control, a file declared as XLSX by either its extension
or MIME type receives the existing non-semantic archive checks: bounded entry count,
bounded compressed and uncompressed sizes, bounded compression ratio, no absolute
or traversal paths, no duplicate normalized names, no encrypted package, and no
active Office content. This XLSX check is not a general file-type allowlist.

This step must not read cells, paragraphs, tables, document text, formulas,
slides, or PDF page text. It must not produce a summary.

Owner: attachment safety validator.

Output: pass/fail only, with a stable safe reason code.

### A4. Atomic workspace materialization

The platform writes bytes only below the run-local `inputs/` root after all
requested files pass A1-A3. The implementation prevents symlinks, collisions,
and path escape, writes files atomically, and removes partial writes on failure.
Files are read-only from the Agent contract perspective.

Owner: sandbox workspace materializer.

Output: ordered metadata and relative `inputs/<basename>` paths.

### A5. Agent-visible metadata

The Agent receives the user request plus a metadata-only manifest naming the
relative input paths, content types, byte counts, and approved retrieval
capabilities. It receives no server-extracted attachment body and no JSON
representation of document content.

Owner: worker and SDK prompt boundary.

Output: one model-safe metadata message, bounded independently of attachment
content.

### A6. Sandbox-owned content processing

The selected Skill decides which original files to read and uses its approved
Sandbox tools/libraries, such as `openpyxl`, `python-docx`, `pypdf`, or the pinned
`firecrawl-anydoc` parser for legacy Office and PowerPoint files. The Skill may inspect
all or a selected portion of a document under Sandbox CPU, memory, disk, timeout, and
network restrictions.

`firecrawl-anydoc` also exposes an explicit `ocr="hosted"` option that can send a
whole OCR-required document to Firecrawl Parse. The platform does not configure
Firecrawl credentials or endpoints and does not invoke hosted OCR. Governed Sandboxes
remain on the fixed internal network with no external egress, and their sole proxy
rejects every route except the existing model and callback contracts. Hosted OCR and
external attachment transmission therefore remain prohibited.

Owner: selected Agent/Skill.

Output: response and/or collected run artifacts.

### A7. Bounded artifact collection

Artifact enumeration, per-file limits, total output limits, filename safety,
and artifact authorization remain platform-owned. No input parsing limit is
repurposed as an output limit.

Owner: artifact collector.

Output: authorized bounded artifacts only.

## Removed Responsibilities

The cutover removes these platform behaviors and their contracts from the production
execution path:

- typed attachment preprocessing requirements and parser evidence;
- parser-era extension/MIME/magic classification as a staging allowlist;
- the Run-execution XLSX parser contract, evidence, and model-context limits;
- `platform_typed_attachment_data` model messages;
- DOCX package inspection, including macro, ActiveX, OLE/CFB, embedded-package,
  relationship, encryption, and archive-structure admission checks;
- PDF parsing, decryption, page-limit, and active-content admission checks;
- the Agent-facing parsed-content retrieval path;
- parser-specific admission failures such as `xlsx_cell_limit_exceeded`.

The former execution parser is not reachable from run dispatch, runtime staging,
or the Sandbox SDK boundary after this cutover. Issue #1273 removes its dead
contract and prompt-budget implementation while retaining the separately
authorized XLSX presentation preview and its bounded parser core. Package-safety
tests move to the raw staging validator. Persisted legacy error codes keep their
public projections for historical Run replay only.

A `stage_context_file_to_workspace` capability may remain only as a raw,
authorized byte-delivery broker for files that were not included in the initial
run input set. It must use A1-A4 and must never return parsed document content.

## Security and Resource Invariants

The cutover does not relax these controls:

- authenticated tenant/workspace/user/session/run scope checks;
- immutable run snapshot membership;
- storage-key confinement and content digest verification;
- safe relative basenames, no path traversal, no symlink writes, and collision
  rejection;
- upload, per-file, total-stage, file-count, sandbox disk, CPU, memory, timeout,
  and artifact collection quotas;
- XLSX archive bomb, encryption, macro, ActiveX, and OLE protection for files
  declared as XLSX by extension or MIME type;
- all other document package structures and contents are opaque to platform
  staging and are handled only by the selected Sandbox Skill;
- no hosted OCR or external document transmission from the local document parser;
- no direct Agent authority to platform storage, databases, Redis, or host
  filesystem;
- public error redaction: no content, file ID, storage key, absolute path, or
  raw exception appears in the browser.

File-size and XLSX archive-structure limits are security/resource boundaries. XLSX cell
count, DOCX paragraph/table count, PDF parsing/page/content processing, and prompt
budget limits are content-processing boundaries and are removed from attachment admission.

## Runtime Contract

For a run with `supplier-change.xlsx` and `process-description.docx`, the
workspace contract is:

```text
inputs/supplier-change.xlsx
inputs/process-description.docx
```

The model receives metadata equivalent to:

```json
{
  "message_kind": "platform_attachment_manifest",
  "attachments": [
    {
      "path": "inputs/supplier-change.xlsx",
      "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "size_bytes": 184312
    },
    {
      "path": "inputs/process-description.docx",
      "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "size_bytes": 90211
    }
  ]
}
```

This example is metadata only. It is not an alternative content-extraction
channel and no attachment text or cell values may be appended to it.

## Upload and staging change contract

This approved change aligns the file path with the raw-file decision above.

Owner: file upload route, storage adapter, context materializer, and Sandbox
workspace-transfer owners.

Bounded paths: `app/routes/files.py`, `app/storage.py`,
`app/context/file_continuity.py`, `app/context/file_content.py`,
`app/runtime/sandbox/container_provider.py`, the owning upload/context tests,
frontend upload configuration, and this ADR. No document parser, Agent/Skill
prompt, public projection, or unrelated storage lifecycle behavior is in scope.

Reached invariants: authenticated tenant/workspace/user/session authorization,
immutable file identity, SHA-256 verification, safe names and paths, archive
resource controls, atomic staging, public error redaction, and Sandbox CPU,
memory, disk, process, and timeout controls remain fail-closed. Multipart parts
are transport fragments only and are never exposed as separate files.

Acceptance: the complete nine-file `3.2.S.3.1-IP266` corpus (approximately
164.18 MiB, with one approximately 63.49 MiB file) can upload and pass Run
admission under the new limits; a 129 MiB input or a 257 MiB input set fails
before workspace writes; staging does not retain the complete input set in
memory; interrupted or expired multipart sessions cannot create a file row;
cross-user and cross-workspace uploads cannot be completed; and the exact
legacy safety regressions remain covered.

Evidence ceiling: local static and focused tests prove source behavior only.
Actual memory use, Sandbox disk capacity, and production object-storage
performance require CI and controlled Docker-host runtime evidence. This change
must stop before raising Run limits if stream staging, real-Sandbox transfer, or
resource-boundary tests fail.

Rollback: revert the release-atomic code and documentation change. Existing
completed file objects and file rows remain readable; incomplete upload sessions
are abortable and may be garbage-collected without changing historical files.

## Limit Matrix

The raw-file contract does not remove resource boundaries. It replaces
content-derived admission limits with byte- and structure-derived limits.

| Boundary | Limit | Enforcement owner | Purpose |
| --- | ---: | --- | --- |
| Stored upload object | 512 MiB | upload session and object storage | bounded persistent object |
| Legacy single-request upload | 32 MiB | upload route | bounded compatibility path |
| Multipart threshold | 32 MiB | upload client and storage adapter | retryable transport |
| Multipart part | 8 MiB | upload client and upload route | bounded request |
| Run input file | 128 MiB | workspace materializer | bounded disk and transfer |
| Run input set | 256 MiB total | workspace materializer | bounded sandbox staging |
| Run input set | 32 files | Run request and materializer | bounded descriptor and disk work |
| Declared XLSX archive | 2,000 entries | archive safety validator | zip-bomb and archive-work bound |
| Declared XLSX compressed entry | 8 MiB | archive safety validator | bounded decompression |
| Declared XLSX expanded package | 32 MiB | archive safety validator | zip-bomb and memory bound |
| Artifact output file | 64 MiB per file | artifact collector | bounded result transfer |
| Artifact output set | 256 MiB total / 128 files | artifact collector | bounded result storage |

There is deliberately no spreadsheet-cell, worksheet-row, worksheet-column,
DOCX-paragraph, DOCX-table-cell, PDF-text, or prompt-token admission limit.
Those are business-content processing concerns that belong to the Skill inside
the sandbox. Sandbox CPU, memory, disk and wall-clock limits remain the
execution bounds for that work.

## Public Failure Contract

The browser distinguishes only safe user-correctable delivery failures, such as
file unavailable, file too large, unsafe declared XLSX package, storage unavailable,
or file-name conflict. New staging no longer emits a parser-supported-type failure.
Persisted historical failures keep their existing projection. The public projection remains
allowlisted and must not expose attachment identity or internals.

## Consequences

Benefits:

- large, valid business workbooks no longer fail because of a platform cell
  count or prompt budget;
- one component, the selected Skill, owns business-document interpretation;
- the Agent sees and processes the original source of truth;
- content cannot be duplicated into an extra model message by the platform;
- parser runtime code, contracts, events, and error mappings are removed from
the production execution path.

Costs:

- Skill authors must use bounded reading strategies appropriate to document
  size;
- Sandbox resource limits become the execution control for content processing;
- the platform does not offer execution-time attachment summaries or content
  search; the separately authorized XLSX presentation preview remains available
  outside Run dispatch.

These costs are intentional. This ADR rejects preview or summary fallback inside
the execution path so all Run attachments retain one auditable, least-authority
behavior.

## Acceptance

- A valid XLSX with more than 2,048 populated cells materializes to `inputs/`
  and can start an Agent run.
- The complete nine-file `3.2.S.3.1-IP266` corpus (approximately 164.18 MiB)
  can upload through Multipart and pass Run admission; a 129 MiB file or a
  257 MiB input set fails before workspace writes.
- Multipart sessions are tenant-, workspace-, and user-bound; duplicate completion,
  expiry, abort, and quota failures do not create file rows or leave live uploads.
- Valid DOC, DOCX, XLS, XLSX, PPT, and PPTX inputs are materialized without
  platform content extraction or a parser-supported-type gate.
- The pinned local Sandbox parser extracts fixed markers from synthetic DOC, XLS,
  PPT, and PPTX fixtures in the candidate image with `ocr="reject"` and container
  networking disabled.
- The SDK input stream contains no `platform_typed_attachment_data` message or
  attachment business content.
- Unsafe declared XLSX archives, oversize input, identity mismatch, scope mismatch,
  and workspace path escape continue to fail before any workspace write.
- Production code has no call path from run dispatch or runtime staging to a
  document-content parser.
- Tests demonstrate raw-file staging, metadata-only Agent delivery, and the
  retained safety failures.

## Rollback

Revert the release-atomic code and documentation change using the repository's
schema rollback procedure. The additive `file_upload_sessions` table and settings
are ignored by older file paths; completed file rows and objects remain readable,
while pending or completing sessions can be aborted and garbage-collected. Do not
raise Run limits in a rollback image that still has the former 32/128 MiB staging
contract.
