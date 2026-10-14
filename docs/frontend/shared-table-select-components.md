# Shared table and single-select components

Status: active frontend source contract. This document does not establish deployment or browser acceptance.

## Components and responsibility

The workbench uses shadcn/ui's composition pattern while retaining its theme:

- `src/components/common/Table.tsx` owns native table, header, body, row and cell primitives. It forwards native attributes and refs. Headers default to `scope="col"`; callers can specify row headers. Existing scroll containers, responsive layouts, cell styles and business actions remain caller-owned.
- `src/components/common/EnterpriseSelect.tsx` composes Radix Select (`@radix-ui/react-select`, pinned in the package and lockfile). Radix owns keyboard navigation, typeahead, disabled-option handling, focus restoration, form participation and popup positioning. Callers provide the current string value, labelled options, accessible name and business callback. Default full-row width uses a zero-specificity rule so caller widths such as `sm:w-40` and `w-auto` remain effective.

Avatar candidate layout remains in AgentAvatarPicker. Its eight choices use four columns at every viewport because the desktop editor can place the picker inside a narrow card; viewport width alone does not establish room for eight fixed-size avatars.

This is a source-adapted component set, not a full shadcn CLI installation. The native Table primitive is sufficient for the current presentation tables; no sort/filter engine is added. Styling uses existing workbench tokens and Tailwind 3. Shadcn attribution is in `frontend/web/THIRD_PARTY_NOTICES.md`.

Complex department/role multiselects, market-tag autocomplete, and model/Skill/MCP catalog panels retain their existing owners and behavior. This migration does not alter ACL identity, provider history, mutations, Run admission or SSE.

## Retirement and compatibility disposition

| Superseded surface | Disposition and current owner |
| --- | --- |
| Handwritten table elements in ToolSelector, AgentBuilderLifecycle, ModelAdminControl, RunMonitorPanel, ExcelPreview and MarkdownContent | All use Table primitives. Markdown copying/export and XLSX validation/image geometry remain in their original owners. |
| SkillManagementTable's div/span table structure | Native Table primitives retain grid/card CSS, row actions, selection, permissions and mobile behavior. |
| Native single selects in SkillDistributionGovernancePanel, ModelAdminControl, SkillsList, AgentBuilderEnterpriseFields and AgentAvatarPicker | All use EnterpriseSelect with the same option values and callbacks. Page size is converted back to number at its owning callback. |
| EnterpriseSelect's handwritten Portal, outside-click listener and coordinate calculation | Removed. The same public component remains the sole Select adapter; MCPServerForm and the five migrated callers use it. |
| `src/components/documents/previews/MermaidDiagram.tsx` | Deleted after source/test/config reference inventory found no caller. Document Markdown already uses MarkdownContent and the active chat MermaidDiagram. No compatibility consumer remains. |
| Native-select test queries and option markup assertions affected by the migration | Updated to the shared trigger/options. Model and builder mounted tests use the existing JSDOM dependency rather than incomplete handwritten DOMs; original request/publication/permission assertions remain. |
| High-specificity Select width and the avatar picker's viewport-based eight-column grid | Default width is lower priority than caller utilities; avatar choices remain four columns inside narrow editor cards. The browser route-layout smoke checks actual filter/search widths and candidate containment/overlap. |
| Upload completion's bare `/api/ai/files/{id}` URL | The upload adapter now supplies `/preview` and a separate `/download`, both retained by the attachment upload hook. The file ID/key is unchanged; no backend migration or URL allowlist expansion is needed. Existing callers without an independent download URL keep their optional-field contract. |

Uploaded-file preview is assembled by the upload adapter, useFileUpload and AttachmentPreviewHost. Markdown uses the owned preview content; XLSX uses the owned preview DTO and downloads original bytes through the independent download endpoint. URL validation, authenticated requests and backend tenant/workspace/user ownership checks remain with their existing owners.

`tableSelectInventory.test.ts` checks production TSX for superseded native table/select markup outside the Table owner and checks the old Mermaid path is absent. This is an absence check, not evidence of browser layout or runtime success. Library-generated hidden form controls are outside this source inventory.

## Owning checks

`test:document-previews` includes shared Select keyboard/form/disabled checks, the retirement inventory and the real multipart-upload → attachment → mounted preview regression for Markdown/XLSX. Model administration, Skill administration, control-plane UI and Run Monitor stages cover their migrated callers. `smoke:route-layout` checks caller width and avatar geometry on rendered pages with synthetic API fixtures. Typecheck, lint and build remain required frontend checks. Browser checks must use synthetic data and must not call production APIs just to inspect component behavior.
