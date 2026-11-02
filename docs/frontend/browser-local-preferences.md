# Browser-local presentation preferences

Theme and sidebar geometry are browser-local presentation choices. Their owner
is the frontend, not the authenticated principal or profile metadata service.

- `ThemeProvider` reads the current `ai-platform-theme` key and the supported
  `lamb-agent-theme` legacy key. A stored light/dark value is an explicit choice.
- Without a stored choice, the initial theme follows the operating system and
  continues following media-query changes. Initial rendering and OS changes do
  not write a preference. A user toggle or `setTheme` persists the explicit
  choice and stops OS following for that mounted provider.
- `useSidebarPreference` owns `ai-platform-sidebar-collapsed`, defaults the
  application shell to expanded, and persists explicit changes. AppContent,
  Agent Builder, Agent Market, and Agent Workspace share this owner.
- The same `useSidebarPreference` owner persists the administrator management
  group's `ai-platform-sidebar-management-collapsed` choice independently of
  sidebar geometry. Management defaults to expanded and explicit collapse or
  expansion survives page-owner remounts; the ordinary-user capabilities group
  keeps its existing initially collapsed behavior. Neither choice grants access.
- `LibreChatShell` owns `ai-platform-context-width` for the desktop file/context panel: default 360px, constrained to 280–520px and the available workspace width. Pointer and keyboard changes persist locally; narrow screens keep the existing Lobe Drawer.
- Storage failures do not prevent the current tab from changing presentation.
  These preferences are deliberately shared by users of the same browser;
  they are not account-scoped server facts or cross-device synchronization.
- Existing local `defaultThinkingLevel` values remain usable as defaults.
  Current session thinking selections and session-restored values are unchanged.

## Retirement and compatibility

The previous theme effect and four sidebar callbacks wrote profile metadata,
but production principal hydration uses `/api/ai/auth/me` and maps only
`display_name` and `source` into user metadata. It never restored these profile
preferences. The server writes, unreachable `applyUserMetadata` bridge, and
`theme:external-change`, `sidebar-collapsed-changed`, and
`thinking-preference-updated` event bridges are retired together.

No server profile route, validation, database value, or metadata key is deleted.
The generic profile metadata API remains for its identified consumers, including
company-navigation favorites. Historical server theme/sidebar/thinking values
are left untouched. This change does not introduce account preference sync,
change identity or authorization, or change PWA/notification behavior.

`test:browser-preferences` is selected by `ci:verify` and owns mounted lifecycle
coverage: OS-derived initialization, OS changes, explicit choice, remount,
legacy values, independent sidebar/management changes, no metadata writes, and
listener cleanup. `GeneralChatEntryReset.mounted` also covers management choices
across actual Chat and non-Chat page owners.
The existing workbench source assertions now point to the local sidebar owner.
