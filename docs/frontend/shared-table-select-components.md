# Shared table and single-select components

Status: active frontend source contract. This document does not establish deployment or browser acceptance.

Lobe UI owns general table and single-select presentation. The [Lobe UI integration contract](lobe-ui-adoption.md) defines versions, providers, public entries and completion conditions. Production callers and local migration checks are complete. The six unused modules and two old direct dependencies are removed; the selector test is named for LobeSelectField.

## Components and responsibility

Management pages import `Table` and `Pagination` directly from `@lobehub/ui/base-ui`. The owning page supplies columns, raw records, row keys, selection, filters, actions and pagination callbacks. Use the library's public scroll/sticky/fixed-column contracts instead of restyling rows as a parallel grid table. Library placeholder and measurement rows are not business records; row assertions should use the owning page's data marker.

`components/lobe-ui/LobeSelectField.tsx` composes Lobe Select with an associated accessible label, caller-owned layout and nested Escape isolation. Lobe owns option navigation, disabled choices, form participation, popup positioning and focus restoration. The adapter preserves the value type; numeric page sizes are converted at their business callback. Width utilities apply to its layout wrapper so the inner full-width Select cannot consume adjacent search space.

Complex department/role selections, market-tag autocomplete and model/Skill/MCP catalogs retain their data and authority owners while using Lobe Select/Popover/AutoComplete/Modal controls. Department selections preserve authority IDs, unresolved values, duplicate restrictions and the 128-choice limit. Expert access editing remains department-only; historical API fields are preserved.

Model user access is employee-only, separate from expert department access.
`ModelUserAccess` uses a compact Lobe Popover trigger with Lobe Radio, Checkbox,
and Input controls, backed by the administrator company employee API
(`companyEmployeesApi`), not the local-user diagnostics API. The trigger shows
whether access is for all authenticated users or a selected-user count; the
popover owns mode selection, search, scrollable employee results, selected-user
summary chips, completion, focus restoration, and Escape handling. Options and
selected labels show name plus work ID; `allowed_user_ids` retains its
historical field name but carries work IDs. Each model owns its server-side
search/page and abortable request; retained selections survive searches and
pagination, and directory failures remain retryable. Publication disables all
access controls; losing management permission clears pending directory state.

Avatar candidates remain four columns inside the editor card. Viewport width alone does not establish room for eight fixed-size avatars. Actual rendered candidates must stay within their buttons without overlap.

## Retirement and compatibility disposition

| Superseded surface | Disposition and current owner |
| --- | --- |
| Generic Table primitives and SkillManagementTable's handwritten structure | The generic Table module is deleted; seven management/parameter/history callers use Lobe Table. Business row selection, actions, permission gates and responsive layout stay with the caller. |
| Markdown/XLSX Table primitive imports | Renderer-owned native semantic tables retain `scope="col"`, copying/export, worksheet validation and image geometry. They are the explicit document-rendering exception. |
| EnterpriseSelect and native ordinary selects | All active ordinary single-select callers use Lobe Select/LobeSelectField with their original option values and callbacks. The old adapter and direct Radix dependency are removed; LobeSelectField.mounted.test.tsx owns the preserved keyboard, focus, form and disabled checks. The existing @assistant-ui/react → radix-ui dependency chain still requires Radix Select 2.3.7; that transitive dependency is retained. |
| Old Checkbox, Pagination, LoadingSpinner and Skill Toggle modules | The modules and obsolete Loading barrel exports are deleted. No compatibility entry point remains; library primitives serve the migrated callers. |
| Unused SkillBaseCard and VideoViewer modules and common barrel exports | Deleted after a full-repository reference inventory found no production or development consumer. SkillsList retains SkillsPanelSkeleton while loading and SkillManagementTable when loaded; all card-base.css rules remain for the skeleton. DocumentPreviewContent and BinaryFilePreview retain native video rendering; ImageViewer remains independent. Absence and active-consumer regressions run in test:document-previews; the old card source-style assertion now checks the active table and retained skeleton CSS. |
| react-hot-toast direct dependency | Removed from the package manifest and lockfile importer. Lobe ToastHost and toast own notifications, including stable queue notification identity. |
| Handwritten department/role popups and market-tag suggestion interaction | Lobe Select/Popover/AutoComplete owns popup interaction; directory resolution, raw identity and tag normalization remain business-owned. The unused `.department-selector` CSS family is removed; DepartmentDirectorySelector and its Skill governance / expert access consumers retain the Lobe controls and current data attributes. |
| Native-select and old table structure assertions | Owning tests use actual library controls and production providers. Business assertions for requests, publication, permissions and ownership remain required. |
| `src/components/documents/previews/MermaidDiagram.tsx` | Previously retired; document Markdown uses MarkdownContent and the active chat MermaidDiagram. Keep the absence check. |
| High-specificity Select width and viewport-based eight-column avatar grid | Caller layout and public library sizing replace the old control CSS. Route-layout smoke measures filter/search widths, visible rail icon sizes and avatar containment. |
| Upload completion's bare `/api/ai/files/{id}` URL | Existing upload adapter contract supplies `/preview` and independent `/download`; file identity and optional download URL compatibility remain unchanged. |

Uploaded-file preview is assembled by the upload adapter, useFileUpload and AttachmentPreviewHost. Markdown uses the owned preview content; XLSX uses the owned preview DTO and downloads original bytes through the independent download endpoint. URL validation, authenticated requests and backend tenant/workspace/user ownership checks remain with their existing owners.

`tableSelectInventory.test.ts` checks active production TSX for old ordinary select/table markup, the eight Lobe Table consumers and the document-renderer exceptions. A separate absence regression checks all six deleted modules, the old test filename and both retired direct dependencies. The same regression bans the retired pagination/Select CSS selectors, handwritten department-selector CSS family and Checkbox animation; it also verifies the active Lobe department selector and both business owners. No deleted module is exempted from the production inventory. The inventory does not establish browser layout or runtime success. Library-generated hidden form controls are outside this source inventory.

## Owning checks

`test:document-previews` includes shared Select keyboard/form/disabled checks, the retirement inventory and the real multipart-upload → attachment → mounted preview regression for Markdown/XLSX. Model administration, Skill administration, control-plane UI and Run Monitor stages cover their migrated callers. `smoke:route-layout` checks caller width and avatar geometry on rendered pages with synthetic API fixtures. Typecheck, lint and build remain required frontend checks. Browser checks must use synthetic data and must not call production APIs just to inspect component behavior.
