import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Routes, Route, useParams, useNavigate } from "react-router-dom";
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
const LandingPage = lazy(() =>
  import("./components/landing/LandingPage").then((m) => ({
    default: m.LandingPage,
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

// Simple page components that set the page title and render AppContent
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
  return <AppContent key="users" activeTab="users" />;
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
  return <AppContent key="settings" activeTab="settings" />;
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
  return <AppContent key="feedback" activeTab="feedback" />;
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
  return <AppContent key="agents" activeTab="agents" />;
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

function PersonaPage() {
  useSEO({
    title: "seo.persona.title",
    description: "seo.persona.description",
    path: "/persona",
  });
  return <AppContent key="persona" activeTab="persona" />;
}

function NotificationsPage() {
  useSEO({
    title: "seo.notifications.title",
    description: "seo.notifications.description",
    path: "/notifications",
  });
  return <AppContent key="notifications" activeTab="notifications" />;
}

function MemoryPage() {
  useSEO({
    title: "seo.memory.title",
    description: "seo.memory.description",
    path: "/memory",
  });
  return <AppContent key="memory" activeTab="memory" />;
}

// Auth page wrapper - redirects to /chat after successful login/register
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
  const { t } = useTranslation();
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
            <Route path="/" element={<LandingPage />} />
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
              path="/skills"
              element={
                <ProtectedRoute
                  permissions={[
                    Permission.SKILL_READ,
                    Permission.MARKETPLACE_READ,
                  ]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <SkillsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/marketplace"
              element={
                <ProtectedRoute
                  permissions={[
                    Permission.SKILL_READ,
                    Permission.MARKETPLACE_READ,
                  ]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <MarketplacePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/mcp"
              element={
                <ProtectedRoute
                  permissions={[Permission.MCP_READ]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <MCPPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/users"
              element={
                <ProtectedRoute
                  permissions={[Permission.USER_READ]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <UsersPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/roles"
              element={
                <ProtectedRoute
                  permissions={[Permission.ROLE_MANAGE]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <RolesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <ProtectedRoute
                  permissions={[Permission.SETTINGS_MANAGE]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <SettingsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/feedback"
              element={
                <ProtectedRoute
                  permissions={[Permission.FEEDBACK_READ]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <FeedbackPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/channels/:channelType?/:instanceId?"
              element={
                <ProtectedRoute
                  permissions={[Permission.CHANNEL_READ]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
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
                  <PersonaPage />
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
                <ProtectedRoute
                  permissions={[Permission.NOTIFICATION_MANAGE]}
                  redirectTo="/chat"
                  showToast
                  toastMessage={t("errors.noPermission")}
                >
                  <NotificationsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/memory"
              element={
                <ProtectedRoute>
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
