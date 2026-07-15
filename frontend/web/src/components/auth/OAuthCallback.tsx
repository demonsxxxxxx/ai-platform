/**
 * OAuth 回调处理页面
 *
 * 处理 OAuth provider code/state 回调，服务器才可建立浏览器会话。
 */

import { useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../hooks/useAuth";
import {
  getRedirectPath,
  clearRedirectPath,
} from "../../services/api";
import { Loading } from "../common";

export function OAuthCallback() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { handleOAuthCallback } = useAuth();
  const callbackGenerationRef = useRef(0);

  useEffect(() => {
    const callbackGeneration = callbackGenerationRef.current + 1;
    callbackGenerationRef.current = callbackGeneration;
    let isCurrent = true;
    const ownsCallback = () =>
      isCurrent && callbackGenerationRef.current === callbackGeneration;

    const handleCallback = async () => {
      // StrictMode cleanup invalidates the first effect before it can mutate
      // auth state; only the remounted generation continues.
      await Promise.resolve();
      if (!ownsCallback()) return;
      const error = searchParams.get("error");
      const code = searchParams.get("code");
      const state = searchParams.get("state");

      if (error) {
        navigate("/auth/login?error=oauth_processing_failed", { replace: true });
        return;
      }

      if (!code || !state || window.location.hash) {
        navigate("/auth/login?error=oauth_no_token", { replace: true });
        return;
      }

      try {
        const callbackOutcome = await handleOAuthCallback(
          searchParams.get("provider") || "unknown",
          code,
          state,
        );
        if (!ownsCallback()) return;
        if (callbackOutcome.status === "cancelled") return;
        if (callbackOutcome.status !== "completed") {
          navigate("/auth/login?error=oauth_processing_failed", {
            replace: true,
          });
          return;
        }

        // 获取重定向路径
        const redirectPath = getRedirectPath() || "/chat";
        clearRedirectPath();

        // 导航到目标页面
        navigate(redirectPath, { replace: true });
      } catch {
        if (!ownsCallback()) return;
        navigate("/auth/login?error=oauth_processing_failed", {
          replace: true,
        });
      }
    };

    void handleCallback();
    return () => {
      isCurrent = false;
    };
  }, [handleOAuthCallback, navigate, searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-stone-50 dark:bg-stone-900">
      <div className="text-center">
        <Loading size="lg" className="justify-center" />
        <p className="mt-4 text-stone-600 dark:text-stone-400">
          {t("auth.completingLogin")}
        </p>
      </div>
    </div>
  );
}

export default OAuthCallback;
