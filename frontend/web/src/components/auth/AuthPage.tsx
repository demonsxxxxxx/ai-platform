/**
 * Phase 1 company login page.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, AtSign } from "lucide-react";
import { PasswordInput } from "./PasswordInput";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";
import { Turnstile } from "react-turnstile";
import { useAuth } from "../../hooks/useAuth";
import { useTheme } from "../../contexts/ThemeContext";
import { Loading, LoadingSpinner } from "../common/LoadingSpinner";
import { ContactAdminDialog } from "../common/ContactAdminDialog";
import { ThemeToggle } from "../common/ThemeToggle";
import { LanguageToggle } from "../common/LanguageToggle";
import { APP_NAME } from "../../constants";
import {
  AUTH_REDIRECT_ANIMATION_MS,
  AUTH_REDIRECT_FAILSAFE_MS,
  resolvePostAuthRedirectPath,
} from "./authRedirectTransition";

interface TurnstileConfig {
  enabled: boolean;
  site_key: string;
  require_on_login: boolean;
}

interface AuthPageProps {
  onSuccess?: (redirectPath?: string) => void;
}

export function AuthPage({ onSuccess }: AuthPageProps) {
  const { t } = useTranslation();
  const { theme } = useTheme();
  const { login } = useAuth();
  const registrationEnabled = false;

  useEffect(() => {
    document.documentElement.classList.add("allow-scroll");
    return () => {
      document.documentElement.classList.remove("allow-scroll");
    };
  }, []);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [contactAdminOpen, setContactAdminOpen] = useState(false);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileKey, setTurnstileKey] = useState(0);
  const [turnstileConfig] = useState<TurnstileConfig>({
    enabled: false,
    site_key: "",
    require_on_login: false,
  });
  const redirectTimerRef = useRef<number | null>(null);
  const redirectFailsafeRef = useRef<number | null>(null);

  useEffect(() => {
    setTurnstileKey((prev) => prev + 1);
  }, [theme]);

  const clearRedirectTimers = () => {
    if (redirectTimerRef.current !== null) {
      window.clearTimeout(redirectTimerRef.current);
      redirectTimerRef.current = null;
    }
    if (redirectFailsafeRef.current !== null) {
      window.clearTimeout(redirectFailsafeRef.current);
      redirectFailsafeRef.current = null;
    }
  };

  useEffect(() => clearRedirectTimers, []);

  const requiresTurnstile = () => {
    return (
      turnstileConfig.enabled &&
      Boolean(turnstileConfig.site_key) &&
      turnstileConfig.require_on_login
    );
  };

  const beginSuccessRedirect = (redirectPath?: string | null) => {
    const nextPath = resolvePostAuthRedirectPath(redirectPath);
    clearRedirectTimers();
    setIsRedirecting(true);

    redirectFailsafeRef.current = window.setTimeout(() => {
      setIsRedirecting(false);
      setIsSubmitting(false);
    }, AUTH_REDIRECT_FAILSAFE_MS);

    redirectTimerRef.current = window.setTimeout(() => {
      try {
        onSuccess?.(nextPath);
      } catch (err) {
        console.error("[AuthPage] Failed to redirect after login:", err);
        setIsRedirecting(false);
        setIsSubmitting(false);
      }
    }, AUTH_REDIRECT_ANIMATION_MS);
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);

    if (!username.trim()) {
      setError(t("auth.enterAccount"));
      return;
    }

    if (!password) {
      setError(t("auth.validation.enterPassword"));
      return;
    }

    if (requiresTurnstile() && !turnstileToken) {
      setError(t("auth.turnstileRequired"));
      return;
    }

    setIsSubmitting(true);
    let startedRedirect = false;

    try {
      const redirectPath = await login(
        { username, password },
        turnstileToken || undefined,
      );
      toast.success(t("auth.loginSuccess"));
      startedRedirect = true;
      beginSuccessRedirect(redirectPath);
    } catch (err) {
      const errorMessage = (err as Error).message || t("auth.operationFailed");

      if (
        errorMessage.includes("请先验证邮箱") ||
        errorMessage.includes("账户未激活")
      ) {
        setContactAdminOpen(true);
      }

      toast.error(errorMessage);
      setError(errorMessage);
      setTurnstileToken(null);
      setTurnstileKey((prev) => prev + 1);
    } finally {
      if (!startedRedirect) {
        setIsSubmitting(false);
      }
    }
  };

  if (isRedirecting) {
    return (
      <div className="auth-shell flex min-h-screen items-center justify-center">
        <div className="text-center">
          <Loading size="lg" className="justify-center" />
          <p className="mt-4 text-stone-600 dark:text-stone-400">
            {t("auth.completingLogin")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-shell min-h-[100svh] min-h-[100dvh] overflow-y-auto overflow-x-hidden">
      <div className="auth-crosshatch" aria-hidden="true" />
      <div className="auth-atmosphere" aria-hidden="true">
        <div className="auth-glow-main absolute -top-24 left-1/2 h-[520px] w-[720px] -translate-x-1/2 bg-[radial-gradient(ellipse_at_center,rgba(251,191,36,0.065)_0%,rgba(251,146,60,0.025)_42%,transparent_70%)] dark:bg-[radial-gradient(ellipse_at_center,rgba(251,191,36,0.04)_0%,rgba(251,146,60,0.018)_42%,transparent_70%)]" />
        <div className="auth-glow-blue absolute left-[4%] top-[34%] h-[360px] w-[360px] bg-[radial-gradient(circle,rgba(56,189,248,0.04)_0%,transparent_62%)] dark:bg-[radial-gradient(circle,rgba(56,189,248,0.028)_0%,transparent_62%)]" />
        <div className="auth-glow-violet absolute bottom-[10%] right-[8%] h-[300px] w-[300px] bg-[radial-gradient(circle,rgba(168,85,247,0.035)_0%,transparent_60%)] dark:bg-[radial-gradient(circle,rgba(168,85,247,0.022)_0%,transparent_60%)]" />
      </div>

      <nav className="fixed inset-x-0 top-0 z-50 border-b border-stone-100/60 bg-white/90 transition-shadow duration-300 dark:border-stone-800/40 dark:bg-stone-950/90">
        <div className="mx-auto flex h-14 max-w-full items-center justify-between px-4 sm:px-8">
          <Link to="/" className="group flex items-center gap-2.5">
            <img
              src="/icons/icon.svg"
              alt={APP_NAME}
              className="h-6 w-6 rounded-lg transition-transform duration-300 group-hover:scale-105 sm:h-7 sm:w-7"
            />
            <span className="font-serif text-[15px] font-bold tracking-tight text-stone-900 dark:text-stone-100 sm:text-lg">
              {APP_NAME}
            </span>
          </Link>
          <div className="flex items-center gap-1.5">
            <LanguageToggle />
            <ThemeToggle />
          </div>
        </div>
      </nav>

      <div className="relative z-10 flex min-h-[100svh] min-h-[100dvh] items-center justify-center px-4 py-20 sm:px-6 sm:py-24">
        <div className="w-full max-w-[22.5rem] sm:max-w-[420px] lg:max-w-[450px] 2xl:max-w-[480px]">
          <div className="mb-5 text-center sm:mb-6">
            <h1 className="mb-2 font-serif text-[2.65rem] font-extrabold leading-[0.9] tracking-[-0.045em] text-stone-900 dark:text-stone-50 sm:text-4xl sm:leading-[0.95] sm:tracking-[-0.03em] lg:text-5xl">
              {APP_NAME}
            </h1>
            <p className="mx-auto max-w-[18rem] text-xs leading-relaxed text-stone-500 dark:text-stone-400 sm:text-[13px] lg:text-sm">
              {t("auth.loginHint")}
            </p>
          </div>

          <div className="auth-panel rounded-[1.35rem] p-4 shadow-stone-200/50 dark:shadow-stone-950/40 sm:rounded-2xl sm:p-6 lg:p-8 2xl:p-10">
            <form
              onSubmit={handleSubmit}
              className="auth-form-animate space-y-4 sm:space-y-5 lg:space-y-6 2xl:space-y-7"
            >
              {error && (
                <div>
                  <div className="flex items-center gap-2 rounded-lg border border-red-200/60 bg-red-50/80 px-2.5 py-1.5 text-xs text-red-600 dark:border-red-900/40 dark:bg-red-950/40 dark:text-red-400 sm:px-3 sm:py-2">
                    <AlertCircle size={14} className="shrink-0" />
                    <span>{error}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setContactAdminOpen(true)}
                    className="mt-1.5 text-xs text-stone-400 transition-colors hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300"
                  >
                    {t("contactAdmin.supportLink", "联系管理员")}
                  </button>
                </div>
              )}

              <div>
                <label className="mb-0.5 block text-[11px] font-medium text-stone-700 dark:text-stone-300 sm:mb-1.5 sm:text-sm">
                  {t("auth.account")}
                </label>
                <div className="relative">
                  <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5 text-stone-400 dark:text-stone-500 sm:pl-3.5">
                    <AtSign size={14} />
                  </div>
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="auth-input w-full rounded-xl py-2.5 pl-10 pr-3 text-sm transition-all sm:py-2.5 sm:pl-10 sm:pr-3 md:py-3 md:pl-11 md:pr-4"
                    placeholder={t("auth.usernameOrEmailPlaceholder")}
                    autoComplete="username"
                  />
                </div>
                <p className="mt-1 text-[10px] text-stone-400 dark:text-stone-500 sm:mt-1.5 sm:text-xs">
                  {t("auth.supportsUsernameOrEmailLogin")}
                </p>
              </div>

              <div>
                <label className="mb-0.5 block text-[11px] font-medium text-stone-700 dark:text-stone-300 sm:mb-1.5 sm:text-sm">
                  {t("auth.password")}
                </label>
                <PasswordInput
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t("auth.passwordPlaceholder")}
                  autoComplete="current-password"
                  showPasswordLabel={t("auth.showPassword")}
                  hidePasswordLabel={t("auth.hidePassword")}
                />
              </div>

              {requiresTurnstile() && (
                <div className="flex justify-center overflow-hidden">
                  <div className="w-full max-w-[300px]">
                    <Turnstile
                      key={turnstileKey}
                      sitekey={turnstileConfig.site_key}
                      onSuccess={(token) => setTurnstileToken(token)}
                      onError={() => {
                        setTurnstileToken(null);
                        setError(t("auth.turnstileError"));
                      }}
                      onExpire={() => setTurnstileToken(null)}
                      theme={theme}
                    />
                  </div>
                </div>
              )}

              <button
                type="submit"
                disabled={isSubmitting || isRedirecting}
                className="auth-primary-button min-h-12 w-full rounded-xl py-3 text-sm font-semibold transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50 lg:py-3.5 2xl:py-4"
              >
                <span className="inline-flex items-center justify-center gap-2">
                  {isSubmitting && (
                    <LoadingSpinner
                      size="sm"
                      className="text-white dark:text-stone-900"
                    />
                  )}
                  <span>{t("auth.login")}</span>
                </span>
              </button>
            </form>

            {!registrationEnabled && (
              <div className="mt-5 text-center text-xs text-stone-500 dark:text-stone-400 sm:text-sm">
                {t("auth.registrationDisabled")}
              </div>
            )}

            <p className="mt-3 text-center text-[10px] text-stone-400 dark:text-stone-500 sm:text-xs">
              {t("auth.termsHint")}
            </p>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-center gap-x-2 text-[10px] text-stone-400 dark:text-stone-500 sm:gap-x-3 sm:text-xs lg:mt-6">
            <span>Powered by {APP_NAME}</span>
            <span className="text-stone-300 dark:text-stone-600">·</span>
            <span>{new Date().getFullYear()}</span>
          </div>
        </div>
      </div>

      <ContactAdminDialog
        isOpen={contactAdminOpen}
        onClose={() => setContactAdminOpen(false)}
        reason="emailActivation"
      />
    </div>
  );
}

export default AuthPage;
