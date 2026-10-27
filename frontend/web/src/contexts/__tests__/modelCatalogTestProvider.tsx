import { createElement, type ReactNode } from "react";
import { AuthProvider } from "../../hooks/useAuth";
import { ModelCatalogProvider } from "../ModelCatalogContext";
import { authApi } from "../../services/api/auth";
import { modelPublicApi } from "../../services/api/modelPublic";
import { modelGroupsApi } from "../../services/api/modelGroups";
import { installBrowserAuthTestDb } from "../../hooks/__tests__/browserAuthTestDb";

export function installModelCatalogTestProvider() {
  installBrowserAuthTestDb();
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, callback: () => Promise<unknown>) => callback(),
  } });
  const originals = { auth: { ...authApi }, models: modelPublicApi.listAvailable, groups: modelGroupsApi.listPublic };
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({
    id: "admin-test", tenant_id: "tenant-test", username: "admin-test", email: "admin@example.test",
    roles: ["admin"], permissions: ["model:admin"], is_admin: true, is_active: true,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  });
  modelGroupsApi.listPublic = async () => ({ groups: [{ id: "group-test", code: "test", name: "测试组", type: "business", enabled: true, revision: 1, is_primary: true, availability: "available", models: [] }], count: 1, version: "model-groups.v1" });
  let calls = 0;
  let fail = false;
  modelPublicApi.listAvailable = async () => {
    calls += 1;
    if (fail) throw new Error("synthetic network failure");
    return { models: [], count: 0, enabled_count: 0 };
  };
  localStorage.setItem("ai_platform_session_present", "synthetic-session");
  return {
    wrap: (children: ReactNode) => createElement(AuthProvider, null, createElement(ModelCatalogProvider, null, children)),
    calls: () => calls,
    failRefresh: () => { fail = true; },
    cleanup: () => {
      Object.assign(authApi, originals.auth);
      modelPublicApi.listAvailable = originals.models;
      modelGroupsApi.listPublic = originals.groups;
      localStorage.removeItem("ai_platform_session_present");
    },
  };
}
