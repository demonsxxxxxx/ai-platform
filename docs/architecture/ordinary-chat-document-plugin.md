# Ordinary chat: preinstalled document plugin

Status: source implementation contract. Owner: Execution; the Claude adapter owns SDK integration.

## User behavior

Ordinary chat can use Anthropic's official `document-skills` plugin to read,
modify and create Word, Excel, PowerPoint and PDF files. No Skill selector or
Expert Agent is required. The model chooses whether to use a document Skill;
ordinary conversation never requires a Skill call or a file attachment.
Generated files still require the existing explicit `attach_file` receipt and
platform artifact validation, persistence and authorized download.

## Installation and ownership

The backend/executor image installs only the upstream `docx`, `xlsx`, `pptx` and
`pdf` folders from the fixed official `anthropics/skills` revision declared by
`app/execution/domain/document_plugin.py`. Its archive digest is checked before
extraction. Skill instructions, scripts, resources and `LICENSE.txt` files remain
unchanged; a local plugin manifest wraps those four folders. No third-party
marketplace, tenant catalog row, synchronization endpoint or retired repository
Skill is introduced. Upstream document Skills are source-available under their
bundled proprietary terms, not Apache-licensed; use and distribution remain
subject to the applicable Anthropic agreement.

The image supplies Node and locked `docx`, `pptxgenjs`, `pdf-lib`, React,
React DOM, `react-icons` and `sharp` packages; the
Python lock supplies `openpyxl`, `pandas`, `markitdown`, Office/XML libraries,
PDF creation/rendering and OCR bindings. LibreOffice Writer/Calc/Impress, Pandoc,
Poppler, qpdf, zip/unzip, Tesseract with Chinese language data, and CJK fonts are
installed in the same image used by native commands. The Node lock narrowly
overrides `pptxgenjs`'s transitive `image-size` to `2.0.3`, fixing
CVE-2025-71329 and CVE-2025-71330; image qualification checks buffer/file dimension
reads, malformed ICNS rejection and PPTX image insertion/rendering.
Native commands keep a fixed credential-free PATH and NODE_PATH; they do not
inherit executor secrets.

## Admission, replay and SDK policy

Only initial server admission grants `_document_plugin_revision` to
`execution_kind=harness_chat` without an Expert Profile. User-supplied values are
stripped or replaced; Skill/Profile runs do not receive the grant. The private
value is frozen in the existing Run input and immutable ExecutionSpec. The worker
passes it through private sandbox configuration and the explicit non-secret
reconciliation input snapshot, so asynchronous terminal settlement validates
Skill evidence against the same frozen grant. The snapshot never infers a grant
from executor results. Runtime validation accepts only the compiled revision and
the image-installed revision marker.

Bootstrap binds image plugin options and the revision-scoped resource-read
callback for the Claude adapter; the adapter does not import Execution's concrete
filesystem implementation. The SDK receives one fixed local plugin path and
exactly `document-skills:docx`, `document-skills:xlsx`, `document-skills:pptx` and
`document-skills:pdf`. Unknown Skill names, plugin paths and source revisions
cannot be selected by the browser. Tenant Skills and Expert Profile Skill sets
retain their own catalog authorization and snapshots; ordinary identity remains
`skill_id=null`, with empty tenant Skill manifests. Plugin availability is not
invocation evidence. Native Skill hooks and Run/Attempt-bound callbacks retain
the existing actual-call lifecycle validation.

Read access extends only to the fixed image plugin's `skills/` tree when the
Run carries the matching grant. Workspace write policy, private roots, tenant,
user, Run/Attempt and lease fences are unchanged. Native commands read the plugin
from the read-only image and write task output only in the scoped workspace.
Raw plugin identities, revision and paths are excluded from ordinary projections;
the answer stream gate replaces the fixed plugin root and revision as well as
Skill identities.

Copy/retry/resume preserve a valid grant from the durable source Run. Historical
Runs without it never gain it during replay. A fresh ordinary turn resolves the
current compiled plugin revision. Revision updates require a reviewed source pin,
archive digest, dependency locks and new image; an old admitted revision is not
silently replaced by a newer package. Mixed old/new executors are unsupported
for newly granted Runs; deploy API, Worker and executor images as one release.
No database migration or historical-data rewrite is needed.

## Change contract and retirement disposition

Scope: initial chat/direct admission, private execution input and sandbox config,
SDK plugin loading/permissions, image dependencies, replay preservation and owning
regressions. No expert permissions, frontend controls, MCP grants, model selection
or external storage authority change. The replaced surface is only the assumption
that every ordinary Run must have no SDK Skill tool; ungranted historical Runs
keep that behavior. ADR 0005's no-synthetic-Skill decision and ADR 0014's six-package
retirement remain. No superseded production implementation or compatibility
alias is introduced.

Regression owners: `test_sandbox_document_capability.py`,
`test_claude_agent_sdk_runner.py`, `test_sandbox_executor_app.py`,
`test_claude_agent_sdk_installed_contract.py`, `test_claude_mcp_registration.py`,
`test_chat_routes.py`, `test_routes.py`, `test_runs_replay.py`,
`test_sandbox_runtime.py`, and `test_packaging_contract.py`. Image/native command
verification must cover real DOCX/PPTX creation and rendering, XLSX formula
recalculation, PDF processing, plugin discovery, and read-only package roots.
Static and mocked tests alone do not establish those runtime capabilities.
