import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  Routes,
  Route,
  useParams,
  useNavigate,
  Navigate,
} from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { useTranslation } from "react-i18next";
import { ProtectedRoute } from "./components/auth/ProtectedRoute";
import { ChatPageSkeleton, FilesPageSkeleton } from "./components/skeletons";
import { ThemeProvider } from "./contexts/ThemeContext";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { SelectionActionPopover } from "./components/common/SelectionActionPopover";
import { useSEO } from "./hooks/usePageTitle";
import { Permission } from "./types";
import { sessionApi } from "./services/api";
import {
  getCachedSessionTitle,
  listenSessionTitleUpdated,
} from "./utils/sessionTitleEvents";
import { APP_TOASTER_CLASS_NAME } from "./components/layout/AppContent/appToastLayout";
import { useAuth } from "./hooks/useAuth";
import type { TabType } from "./components/layout/AppContent/types";

const SharedPage = lazy(() =>
  import("./components/share/SharedPage").then((m) => ({
    default: m.SharedPage,
  })),
);
const OAuthCallback = lazy(() =>
  import("./components/auth/OAuthCallback").then((m) => ({
    default: m.OAuthCallback,
  })),
);
const ForgotPassword = lazy(() =>
  import("./components/auth/ForgotPassword").then((m) => ({
    default: m.ForgotPassword,
  })),
);
const ResetPassword = lazy(() =>
  import("./components/auth/ResetPassword").then((m) => ({
    default: m.ResetPassword,
  })),
);
const VerifyEmail = lazy(() =>
  import("./components/auth/VerifyEmail").then((m) => ({
    default: m.VerifyEmail,
  })),
);
const RegistrationPending = lazy(() =>
  import("./components/auth/RegistrationPending").then((m) => ({
    default: m.RegistrationPending,
  })),
);
const AuthPage = lazy(() =>
  import("./components/auth/AuthPage").then((m) => ({ default: m.AuthPage })),
);
const AppContent = lazy(() =>
  import("./components/layout/AppContent/index").then((m) => ({
    default: m.AppContent,
  })),
);
const NotFoundPage = lazy(() =>
  import("./components/common/NotFoundPage").then((m) => ({
    default: m.NotFoundPage,
  })),
);

function ChatPageSEO() {
  const { sessionId } = useParams<{ sessionId?: string }>();
  const [sessionName, setSessionName] = useState<string | null>(null);
  const prevSessionIdRef = useRef<string | null>(null);

  // Fetch session name when sessionId changes
  useEffect(() => {
    if (!sessionId) {
      setSessionName(null);
      prevSessionIdRef.current = null;
      return;
    }

    // Reset only when switching to a different session
    if (sessionId !== prevSessionIdRef.current) {
      setSessionName(null);
      prevSessionIdRef.current = sessionId;
    }

    const fetchSessionName = async () => {
      try {
        const session = await sessionApi.get(sessionId);
        if (session?.name) {
          setSessionName(session.name);
        }
      } catch (err) {
        console.warn("[ChatPage] Failed to fetch session:", err);
      }
    };

    fetchSessionName();
  }, [sessionId]);

  // React immediately when generateTitle finishes in the active chat session.
  useEffect(() => {
    if (!sessionId) return;

    const cachedTitle = getCachedSessionTitle(sessionId);
    if (cachedTitle) {
      setSessionName(cachedTitle);
    }

    return listenSessionTitleUpdated((detail) => {
      if (detail.sessionId === sessionId) {
        setSessionName(detail.title);
      }
    });
  }, [sessionId]);

  // Poll for session name after initial load (handles race with generate-title)
  useEffect(() => {
    if (!sessionId || sessionName) return;

    const delay = setTimeout(() => {
      sessionApi
        .get(sessionId)
        .then((session) => {
          if (session?.name) setSessionName(session.name);
        })
        .catch(() => {});
    }, 3000);

    return () => clearTimeout(delay);
  }, [sessionId, sessionName]);

  // Use session name if available, otherwise use default "nav.chat"
  useSEO({
    title: sessionName || "seo.chat.title",
    description: "seo.chat.description",
    path: sessionId ? `/chat/${sessionId}` : "/chat",
  });

  return null;
}

// Chat Page Component
function ChatPage() {
  return (
    <>
      <ChatPageSEO />
      <AppContent key="chat" activeTab="chat" />
    </>
  );
}

function RootRedirect() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return <ChatPageSkeleton />;
  }

  return isAuthenticated ? (
    <Navigate to="/chat" replace />
  ) : (
    <Navigate to="/auth/login" replace />
  );
}

// Simple page components that set the page title and render AppContent
function LaunchpadPage() {
  useSEO({
    title: "seo.apps.title",
    description: "seo.apps.description",
    path: "/apps",
  });
  return <AppContent key="apps" activeTab="apps" />;
}

function SkillsPage() {
  useSEO({
    title: "seo.skills.title",
    description: "seo.skills.description",
    path: "/skills",
  });
  return <AppContent key="skills" activeTab="skills" />;
}

function MarketplacePage() {
  useSEO({
    title: "seo.marketplace.title",
    description: "seo.marketplace.description",
    path: "/marketplace",
  });
  return <AppContent key="marketplace" activeTab="marketplace" />;
}

function UsersPage() {
  useSEO({
    title: "seo.users.title",
    description: "seo.users.description",
    path: "/users",
  });
  return <PhaseTwoWorkbenchPage activeTab="users" />;
}

function RolesPage() {
  useSEO({
    title: "seo.roles.title",
    description: "seo.roles.description",
    path: "/roles",
  });
  return <AppContent key="roles" activeTab="roles" />;
}

function SettingsPage() {
  useSEO({
    title: "seo.settings.title",
    description: "seo.settings.description",
    path: "/settings",
  });
  return <PhaseTwoWorkbenchPage activeTab="settings" />;
}

function MCPPage() {
  useSEO({
    title: "seo.mcp.title",
    description: "seo.mcp.description",
    path: "/mcp",
  });
  return <AppContent key="mcp" activeTab="mcp" />;
}

function FeedbackPage() {
  useSEO({
    title: "seo.feedback.title",
    description: "seo.feedback.description",
    path: "/feedback",
  });
  return <PhaseTwoWorkbenchPage activeTab="feedback" />;
}

function ChannelsPage() {
  useSEO({
    title: "seo.channels.title",
    description: "seo.channels.description",
    path: "/channels",
  });
  return <AppContent key="channels" activeTab="channels" />;
}

function AgentsPage() {
  useSEO({
    title: "seo.agents.title",
    description: "seo.agents.description",
    path: "/agents",
  });
  return <PhaseTwoWorkbenchPage activeTab="agents" />;
}

function ModelsPage() {
  useSEO({
    title: "seo.models.title",
    description: "seo.models.description",
    path: "/models",
  });
  return <AppContent key="models" activeTab="models" />;
}

function FilesPage() {
  useSEO({
    title: "seo.files.title",
    description: "seo.files.description",
    path: "/files",
  });
  return <AppContent key="files" activeTab="files" />;
}

function NotificationsPage() {
  useSEO({
    title: "seo.notifications.title",
    description: "seo.notifications.description",
    path: "/notifications",
  });
  return <PhaseTwoWorkbenchPage activeTab="notifications" />;
}

function MemoryPage() {
  useSEO({
    title: "seo.memory.title",
    description: "seo.memory.description",
    path: "/memory",
  });
  return <AppContent key="memory" activeTab="memory" />;
}

function WorkbenchForbiddenPage({
  activeTab,
  permissionLabel,
}: {
  activeTab: Exclude<TabType, "chat">;
  permissionLabel: string;
}) {
  const { t } = useTranslation();

  return (
    <AppContent
      key={`${activeTab}-forbidden`}
      activeTab={activeTab}
      routeUnavailable={{
        state: "forbidden",
        title: t("workbench.forbidden.title", {
          page: t(`launchpad.apps.${activeTab}`),
        }),
        description: t("workbench.forbidden.description", {
          permission: permissionLabel,
        }),
        surface: `${activeTab}-route-permission`,
      }}
    />
  );
}

function PhaseTwoWorkbenchPage({
  activeTab,
}: {
  activeTab: Exclude<TabType, "chat">;
}) {
  const { t } = useTranslation();

  return (
    <AppContent
      key={`${activeTab}-phase2`}
      activeTab={activeTab}
      routeUnavailable={{
        state: "degraded",
        title: t("workbench.phaseTwo.title", {
          page: t(`nav.${activeTab}`),
        }),
        description: t("workbench.phaseTwo.description"),
        surface: `${activeTab}-phase2-backend-projection`,
      }}
    />
  );
}

// Auth page wrapper - redirects to the chat-first workbench after successful login/register
function AuthPageWrapper({
  initialMode,
}: {
  initialMode?: "login" | "register";
}) {
  const navigate = useNavigate();
  useSEO({
    title: initialMode === "register" ? "auth.register" : "auth.login",
    path: initialMode === "register" ? "/auth/register" : "/auth/login",
    noindex: true,
  });
  return (
    <AuthPage
      initialMode={initialMode}
      onSuccess={(redirectPath) =>
        navigate(redirectPath ?? "/chat", { replace: true })
      }
    />
  );
}

// Main App Component
function App() {
  return (
    <ThemeProvider>
      <ErrorBoundary>
        <Toaster
          position="top-center"
          containerClassName={APP_TOASTER_CLASS_NAME}
          containerStyle={{ top: "56px" }}
          toastOptions={{
            duration: 4000,
            style: {
              background: "#333",
              color: "#fff",
              borderRadius: "8px",
              padding: "12px 16px",
              minWidth: "280px",
            },
            success: {
              duration: 3000,
              iconTheme: {
                primary: "#22c55e",
                secondary: "#fff",
              },
            },
            error: {
              duration: 5000,
              iconTheme: {
                primary: "#ef4444",
                secondary: "#fff",
              },
            },
          }}
        />
        <SelectionActionPopover />
        <Suspense fallback={<ChatPageSkeleton />}>
          <Routes>
            <Route path="/" element={<RootRedirect />} />
            {/* Auth routes */}
            <Route path="/auth/login" element={<AuthPageWrapper />} />
            <Route
              path="/auth/register"
              element={<AuthPageWrapper initialMode="register" />}
            />
            <Route
              path="/chat/:sessionId?"
              element={
                <ProtectedRoute>
                  <ChatPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/apps"
              element={
                <ProtectedRoute>
                  <LaunchpadPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/skills"
              element={
                <ProtectedRoute>
                  <SkillsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/marketplace"
              element={
                <ProtectedRoute>
                  <MarketplacePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/mcp"
              element={
                <ProtectedRoute>
                  <MCPPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/users"
              element={
                <ProtectedRoute>
                  <UsersPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/roles"
              element={
                <ProtectedRoute
                  permissions={[
                    Permission.ROLE_MANAGE,
                    Permission.SETTINGS_MANAGE,
                    Permission.AGENT_ADMIN,
                    Permission.ADMIN_STATUS,
                  ]}
                  fallbackComponent={
                    <WorkbenchForbiddenPage
                      activeTab="roles"
                      permissionLabel={`${Permission.ROLE_MANAGE} / ${Permission.SETTINGS_MANAGE} / ${Permission.AGENT_ADMIN} / ${Permission.ADMIN_STATUS}`}
                    />
                  }
                >
                  <RolesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute>
                  <SettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/feedback"
              element={
                <ProtectedRoute>
                  <FeedbackPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/channels/:channelType?/:instanceId?"
              element={
                <ProtectedRoute>
                  <ChannelsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/agents"
              element={
                <ProtectedRoute>
                  <AgentsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/models"
              element={
                <ProtectedRoute>
                  <ModelsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/persona"
              element={
                <ProtectedRoute>
                  <Navigate to="/marketplace" replace />
                </ProtectedRoute>
              }
            />
            <Route
              path="/files"
              element={
                <ProtectedRoute loadingComponent={<FilesPageSkeleton />}>
                  <FilesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/notifications"
              element={
                <ProtectedRoute>
                  <NotificationsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/memory"
              element={
                <ProtectedRoute
                  permissions={[
                    Permission.CHAT_READ,
                    Permission.SESSION_READ,
                  ]}
                  fallbackComponent={
                    <WorkbenchForbiddenPage
                      activeTab="memory"
                      permissionLabel={`${Permission.CHAT_READ} / ${Permission.SESSION_READ}`}
                    />
                  }
                >
                  <MemoryPage />
                </ProtectedRoute>
              }
            />
            {/* OAuth callback page - handles OAuth redirect from backend */}
            <Route path="/auth/callback" element={<OAuthCallback />} />
            {/* Password reset pages - no auth required */}
            <Route path="/auth/reset-request" element={<ForgotPassword />} />
            <Route path="/auth/reset-password" element={<ResetPassword />} />
            {/* Email verification page - no auth required */}
            <Route path="/auth/verify-email" element={<VerifyEmail />} />
            {/* Registration pending verification page - no auth required */}
            <Route path="/auth/pending" element={<RegistrationPending />} />
            {/* Public shared session page - no auth required */}
            <Route
              path="/shared/:shareId"
              element={
                <Suspense fallback={null}>
                  <SharedPage />
                </Suspense>
              }
            />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
    </ThemeProvider>
  );
}

export default App;
