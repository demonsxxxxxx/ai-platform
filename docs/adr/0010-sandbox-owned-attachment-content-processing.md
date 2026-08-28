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
  -> bounded byte and structural-safety validation
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

### A3. Structural package safety

For container formats such as DOCX/XLSX, the platform performs only
non-semantic archive safety checks: bounded entry count, bounded compressed and
uncompressed sizes, bounded compression ratio, no absolute or traversal paths,
no duplicate normalized names, and no encrypted package. Office active content,
macros, ActiveX, and OLE objects remain denied. Opaque embedded package entries
and external relationships are neither interpreted nor dereferenced by the
platform. The selected Skill may inspect accepted original bytes only inside
its sandbox and under its existing tool and network policy.

This step must not read cells, paragraphs, tables, document text, formulas, or
PDF page text. It must not produce a summary.

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
sandbox tools/libraries, such as `openpyxl`, `python-docx`, or `pypdf`. It may
inspect all or a selected portion of a document under Sandbox CPU, memory,
disk, timeout, and network restrictions.

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
- the Run-execution XLSX parser contract, evidence, and model-context limits;
- `platform_typed_attachment_data` model messages;
- server parsing of DOCX/PDF/text as a prerequisite for attachment staging;
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
- archive bomb, encryption, macro, ActiveX, and OLE protection;
- embedded packages and external relationships are never interpreted, executed,
  or dereferenced by the platform;
- no direct Agent authority to platform storage, databases, Redis, or host
  filesystem;
- public error redaction: no content, file ID, storage key, absolute path, or
  raw exception appears in the browser.

File-size and archive-structure limits are security/resource boundaries. XLSX
cell count, DOCX paragraph/table count, PDF page text extraction, and prompt
budget limits are content-processing boundaries and are removed from attachment
admission.

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

## Limit Matrix

The raw-file contract does not remove resource boundaries. It replaces
content-derived admission limits with byte- and structure-derived limits.

| Boundary | Limit | Enforcement owner | Purpose |
| --- | ---: | --- | --- |
| Run input file | 32 MiB per file | workspace materializer | bounded disk and transfer |
| Run input set | 128 MiB total | workspace materializer | bounded sandbox staging |
| Run input set | 512 files | workspace materializer | bounded descriptor and disk work |
| XLSX archive / DOCX outer plus embedded archives | 2,000 cumulative entries | archive safety validator | zip-bomb and archive-work bound |
| XLSX compressed entry | 8 MiB | archive safety validator | bounded decompression |
| XLSX expanded package | 32 MiB | archive safety validator | zip-bomb and memory bound |
| DOCX compressed entry | 32 MiB | archive safety validator | bounded decompression |
| DOCX outer plus embedded archives | 64 MiB cumulative expanded bytes | archive safety validator | zip-bomb and memory bound |
| Artifact output file | 64 MiB per file | artifact collector | bounded result transfer |
| Artifact output set | 256 MiB total / 128 files | artifact collector | bounded result storage |

There is deliberately no spreadsheet-cell, worksheet-row, worksheet-column,
DOCX-paragraph, DOCX-table-cell, PDF-text, or prompt-token admission limit.
Those are business-content processing concerns that belong to the Skill inside
the sandbox. Sandbox CPU, memory, disk and wall-clock limits remain the
execution bounds for that work.

## Public Failure Contract

The browser distinguishes only safe user-correctable delivery failures, such as
file unavailable, file too large, unsupported/unsafe package, storage
unavailable, or file-name conflict. It does not display parser-specific errors
because platform content parsing is removed. The public projection remains
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
- A valid DOCX is materialized without platform paragraph/table extraction.
- The SDK input stream contains no `platform_typed_attachment_data` message or
  attachment business content.
- Unsafe archives, oversize input, identity mismatch, scope mismatch, and
  workspace path escape continue to fail before any workspace write.
- Production code has no call path from run dispatch or runtime staging to a
  document-content parser.
- Tests demonstrate raw-file staging, metadata-only Agent delivery, and the
  retained safety failures.

## Rollback

Revert the release-atomic PR. No data migration, schema change, or persisted
parser state is introduced by this decision.
