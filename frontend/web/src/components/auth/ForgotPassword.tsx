import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { Mail, CheckCircle } from "lucide-react";
import { BackIcon } from "../common/BackIcon";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";
import { authApi } from "../../services/api";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { ThemeToggle } from "../common/ThemeToggle";
import { LanguageToggle } from "../common/LanguageToggle";
import { APP_NAME } from "../../constants";

export function ForgotPassword() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);

  const handleBackToLogin = () => navigate("/auth/login");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) {
      toast.error(t("auth.emailRequired"));
      return;
    }
    setIsSubmitting(true);
    try {
      await authApi.forgotPassword(email);
      setIsSuccess(true);
      toast.success(t("auth.forgotPasswordSuccess"));
    } catch (err) {
      toast.error((err as Error).message || t("auth.operationFailed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="auth-shell min-h-[100svh] min-h-[100dvh] overflow-y-auto overflow-x-hidden">
      <div className="auth-crosshatch" aria-hidden="true" />

      <nav className="fixed top-0 inset-x-0 z-50 bg-white/90 dark:bg-stone-950/90 border-b border-stone-100/60 dark:border-stone-800/40 transition-shadow duration-300">
        <div className="mx-auto flex h-14 max-w-full items-center justify-between px-4 sm:px-8">
          <Link to="/" className="flex items-center gap-2.5 group">
            <img
              src="/icons/icon.svg"
              alt={APP_NAME}
              className="w-6 h-6 rounded-md"
            />
            <span className="text-[15px] font-semibold text-stone-900 dark:text-stone-100">
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
        <div className="w-full max-w-[22.5rem] sm:max-w-[380px]">
          {isSuccess ? (
            <>
              <div className="mb-5 text-center">
                <div className="auth-status-icon relative mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50 dark:bg-emerald-900/20">
                  <CheckCircle className="h-6 w-6 text-emerald-600 dark:text-emerald-400" />
                </div>
                <h1 className="text-2xl font-semibold text-stone-900 dark:text-stone-100 mb-1">
                  {t("auth.checkYourEmail")}
                </h1>
                <p className="text-sm leading-relaxed text-stone-400 dark:text-stone-500">
                  {t("auth.forgotPasswordEmailSent")}
                </p>
              </div>
              <button
                onClick={handleBackToLogin}
                className="auth-primary-button min-h-12 w-full rounded-lg py-3 text-sm transition-colors duration-150"
              >
                <span className="inline-flex items-center justify-center gap-2">
                  <BackIcon size={14} />
                  {t("auth.backToLogin")}
                </span>
              </button>
            </>
          ) : (
            <>
              <div className="mb-5 text-center">
                <h1 className="text-2xl font-semibold text-stone-900 dark:text-stone-100 mb-1">
                  {t("auth.forgotPassword")}
                </h1>
                <p className="text-sm leading-relaxed text-stone-400 dark:text-stone-500">
                  {t("auth.forgotPasswordDesc")}
                </p>
              </div>

              <div className="auth-panel rounded-xl p-4 sm:p-6">
                <form onSubmit={handleSubmit} className="space-y-4">
                  <div>
                    <label className="mb-1.5 block text-[13px] font-medium text-stone-600 dark:text-stone-400">
                      {t("auth.email")}
                    </label>
                    <div className="relative">
                      <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-stone-400 dark:text-stone-500">
                        <Mail size={16} />
                      </div>
                      <input
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        className="auth-input w-full rounded-lg py-2.5 pl-10 pr-3 text-sm"
                        placeholder={t("auth.emailPlaceholder")}
                      />
                    </div>
                  </div>
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="auth-primary-button min-h-12 w-full rounded-lg py-3 text-sm transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className="inline-flex items-center justify-center gap-2">
                      {isSubmitting && (
                        <LoadingSpinner
                          size="sm"
                          className="text-white dark:text-stone-900"
                        />
                      )}
                      <span>{t("auth.sendResetEmail")}</span>
                    </span>
                  </button>
                </form>
              </div>

              <div className="mt-3 text-center">
                <button
                  onClick={handleBackToLogin}
                  className="inline-flex items-center gap-1.5 text-[13px] text-stone-400 transition-colors hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300"
                >
                  <BackIcon size={12} />
                  {t("auth.backToLogin")}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default ForgotPassword;
