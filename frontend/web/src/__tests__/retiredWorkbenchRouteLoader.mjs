/**
 * Keep App, ProtectedRoute, AuthProvider, AppContent, NonChatAppContent,
 * AppShell, TabContent and WorkbenchMenuHost real. Only unrelated heavy
 * descendants and redirect destinations are isolated from these route tests.
 */
const isolatedModules = new Map([
  ["components/layout/AppContent/ChatAppContent.tsx", "ChatAppContent"],
  ["components/panels/SessionSidebar.tsx", "SessionSidebar"],
  ["components/auth/AuthPage.tsx", "AuthPage"],
  ["features/agent-market/AgentMarketRoute.tsx", "AgentMarketRoute"],
]);

export async function load(url, context, nextLoad) {
  const pathname = new URL(url).pathname;
  const isolated = Array.from(isolatedModules).find(([path]) =>
    pathname.endsWith(`/src/${path}`),
  );
  if (isolated) {
    const [, name] = isolated;
    return {
      format: "module",
      source: `import { createElement } from "react";
        export function ${name}() {
          return createElement("div", { "data-retired-route-test-screen": "${name}" });
        }`,
      shortCircuit: true,
    };
  }
  return nextLoad(url, context);
}
