// Development-only acceptance fixture: real components; synthetic in-memory API seams.
import { useCallback, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { ToastHost } from "@lobehub/ui/base-ui";
import { ThemeProvider } from "../../src/contexts/ThemeContext";
import { LobeUiProvider } from "../../src/contexts/LobeUiProvider";
import { AuthProvider, useAuth } from "../../src/hooks/useAuth";
import { useSidebarPreference } from "../../src/hooks/useSidebarPreference";
import { SidebarSkeleton } from "../../src/components/skeletons/SidebarSkeleton";
import { SessionSidebar } from "../../src/components/panels/SessionSidebar";
import ExcalidrawPreview from "../../src/components/documents/previews/ExcalidrawPreview";
import { authApi, sessionApi, type BackendSession } from "../../src/services/api";
import type { SessionListResponse } from "../../src/services/api/session";
import "../../src/i18n";
import "../../src/styles/tailwind.css";
import "../../src/styles/base.css";
import "../../src/styles/animations.css";
import "../../src/styles/components.css";
import "../../src/styles/auth.css";
import "../../src/styles/chat.css";
import "../../src/styles/skill.css";
import "../../src/styles/skill-management-table.css";
import "../../src/styles/card-base.css";
import "../../src/styles/welcome.css";
import "../../src/styles/syntax-highlight.css";
import "../../src/styles/markdown.css";
import "../../src/styles/utilities.css";

const noop = () => {};
const ready = async () => {};
const params = new URLSearchParams(location.search);
localStorage.setItem("language", "zh");
localStorage.setItem("i18nextLng", "zh");
localStorage.setItem("ai-platform-theme", params.get("theme") === "dark" ? "dark" : "light");
localStorage.setItem("ai-platform-sidebar-collapsed", params.get("collapsed") === "true" ? "true" : "false");
const makeSession = (id: string): BackendSession => ({ id, name: `Synthetic session ${id}`, agent_id: "general-agent", is_active: true, metadata: {}, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z" });
const searches: Array<{ query: string; resolve: (value: SessionListResponse) => void }> = [];
const deletions: Array<{ id: string; resolve: (value: Response) => void; reject: (error: Error) => void }> = [];
const state = {
  errors: [] as string[], searches, deletions, selected: "", removed: [] as string[],
  resolveSearch(index: number, id: string) { searches[index].resolve({ sessions: [makeSession(id)], total: 1, skip: 0, limit: 30, has_more: false }); },
  resolveDelete(index: number, success: boolean) { if (success) deletions[index].resolve(new Response(null, { status: 204 })); else deletions[index].reject(new Error("Synthetic deletion failure")); },
};
Object.assign(window, { __sessionPreview: state });
window.addEventListener("error", event => state.errors.push(String(event.message)));
window.addEventListener("unhandledrejection", event => state.errors.push(String(event.reason)));
// No fallback to a backend: every unstubbed fetch is a test failure.
window.fetch = async (input) => { const path = typeof input === "string" ? input : input instanceof URL ? input.href : input.url; state.errors.push(`unstubbed_fetch:${path}`); throw new Error("Synthetic fixture blocks unstubbed fetch"); };
authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request.generation });
authApi.getCurrentUser = async () => ({ id: "synthetic-user", tenant_id: "synthetic-tenant", username: "Synthetic user", email: "synthetic@example.test", roles: [], permissions: [], is_active: true, is_admin: false, created_at: "", updated_at: "" });
sessionApi.list = async options => new Promise(resolve => searches.push({ query: options?.search ?? "", resolve }));
sessionApi.delete = async id => new Promise((resolve, reject) => deletions.push({ id, resolve, reject }));
sessionApi.listAuthoritative = async () => { state.errors.push("unexpected_default_session_list"); throw new Error("Use the supplied synthetic session source"); };
const diagram = JSON.stringify({ elements: [{ id: "synthetic-wide-rectangle", type: "rectangle", x: 0, y: 0, width: 1600, height: 900, angle: 0, strokeColor: "#1971c2", backgroundColor: "#a5d8ff", fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid", roughness: 0, opacity: 100, groupIds: [], frameId: null, roundness: null, seed: 1, version: 1, versionNonce: 1, isDeleted: false, boundElements: null, updated: 1, link: null, locked: false }], appState: { viewBackgroundColor: "#ffffff" } });

export function Fixture() {
  const { user } = useAuth();
  const [collapsed, setCollapsed] = useSidebarPreference();
  const [sessions, setSessions] = useState([makeSession("a"), makeSession("b")]);
  const [mobileOpen, setMobileOpen] = useState(true);
  const [selected, setSelected] = useState("a");
  const removeSession = useCallback((id: string) => { state.removed.push(id); setSessions(items => items.filter(item => item.id !== id)); }, []);
  const source = useMemo(() => ({ sessions, isLoading: false, isLoadingMore: false, hasMore: false, loadMore: ready, softRefresh: ready, prependSession: noop, updateSession: noop, removeSession }), [sessions, removeSession]);
  const mode = params.get("mode");
  return <div data-synthetic-auth-ready={user?.id} style={{ display: "flex", height: "100dvh", background: "var(--theme-bg)", color: "var(--theme-text)" }}>
    {mode === "skeleton" ? <section data-synthetic-skeleton style={{ display: "flex", height: "100%" }}><SidebarSkeleton /></section> : mode !== "diagram" ?
      <SessionSidebar currentSessionId={selected} onSelectSession={id => { state.selected = id; setSelected(id); }} onNewSession={() => {}} isCollapsed={collapsed} onToggleCollapsed={setCollapsed} mobileOpen={mobileOpen} onMobileOpen={() => setMobileOpen(true)} onMobileClose={() => setMobileOpen(false)} sessionSource={source} /> : null}
    <main style={{ flex: 1, minWidth: 0, height: "100%" }}>
      {mode === "diagram" ? <section data-synthetic-diagram style={{ height: "100%" }}><ExcalidrawPreview data={diagram} /></section> : <div style={{ padding: 24 }}><p>Synthetic session acceptance — {mode === "skeleton" ? "loading" : "loaded"}</p>{mode !== "skeleton" && <button data-synthetic-open-sidebar onClick={() => setMobileOpen(true)}>Open synthetic sidebar</button>}</div>}
    </main><ToastHost />
  </div>;
}
createRoot(document.getElementById("root")!).render(<ThemeProvider><LobeUiProvider><MemoryRouter><AuthProvider><Fixture /></AuthProvider></MemoryRouter></LobeUiProvider></ThemeProvider>);
