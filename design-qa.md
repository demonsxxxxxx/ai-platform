**Comparison target**

- Source visual truth: `/var/folders/vg/7f50x74j1nd2qlcy1cvww3_80000gn/T/codex-clipboard-59284f5f-0668-4afa-9df0-e359323d6ea1.png`
- Source pixels: 2492 x 1488 RGBA. The source includes its own application chrome and represents the requested catalog/detail hierarchy, not an exact production-shell snapshot.
- Implementation screenshot: `/Users/jiangxinlin/Documents/Codex/ai-platform-issue-868/.codex-tmp/route-layout-browser-smoke-focused/desktop-skills.png`
- Implementation pixels and CSS viewport: 1440 x 900 at device scale factor 1.
- State: authenticated administrator, light theme, one selected Skill, authoritative department selector open, mock-backed APIs.
- Normalization: full viewport comparison with application chrome retained in both images. The source was inspected at its native aspect ratio; no density-only differences were treated as defects.

**Full-view comparison evidence**

- The implementation preserves the source hierarchy: governance status, one catalog toolbar, one canonical selected row, and a selected-Skill detail with release and user-visibility controls.
- The production sidebar and header intentionally differ from the cropped reference chrome; the requested content uses the existing application shell and theme tokens.
- Desktop uses a balanced master-detail grid. The 768 px and 390 x 844 browser-smoke captures collapse to one column and remain vertically reachable without body-level horizontal overflow.

**Focused region comparison evidence**

- Catalog/detail region: the implementation removes the reference's second independent Skill list while retaining the selected detail, which is the issue's required information-architecture correction.
- Scope editor: status, ordinary-user visibility, explicit all/restricted department mode, authoritative department multi-select, roles, save, error, and success states use existing controls and Lucide icons.
- Typography uses the existing product font stack and weights. Spacing, borders, radii, colors, and shadows use existing theme tokens. No image assets or custom SVG/CSS drawings were introduced.
- Product-facing copy removes tenant and internal capability-distribution terminology in favor of catalog status, user visibility, department scope, and role scope.

**Comparison history**

- Initial browser pass found the department popover extended below the 768 px viewport after scrolling to the end (P2 clipping).
- Fix: the desktop/tablet popover now opens above its trigger; the existing fixed mobile treatment remains unchanged.
- Post-fix evidence: the complete mock-backed route smoke passed 15 route/viewport cases. The focused desktop pass reports the department menu rect fully within the viewport and no horizontal overflow.
- Independent fixed-head review found that the first implementation selected rows by display name while the authorization writer requires the stable Skill id. The catalog now merges admin lifecycle records and the runtime projection behind the stable id, keeps draft/unpublished records visible, and disables runtime-only actions when no runtime projection exists.
- Validation was hardened so unstubbed API calls fail the smoke, required controls and requests are asserted per route, and the Skill scenario submits a department-scope save to an opaque Skill id.

**Findings**

- No actionable P0, P1, or P2 visual differences remain.
- The production shell's navigation width and chrome are intentional product constraints rather than design drift.

**Implementation Checklist**

- [x] One canonical Skill list and selected detail.
- [x] Controlled distribution editor with fail-closed directory authority.
- [x] Responsive master-detail and single-column mobile layout.
- [x] One primary `/skills` vertical scroller and preserved Chat scrollers.
- [x] Responsive outer gutters across Skill, Market, workspace loading, and Builder states.
- [x] Browser evidence for vertical reachability, horizontal overflow, and popover visibility.

**Follow-up Polish**

- None required for this issue.

final result: passed
