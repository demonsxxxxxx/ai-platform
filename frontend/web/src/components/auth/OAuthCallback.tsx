/**
 * OAuth 回调处理页面
 *
 * 处理 OAuth 提供商重定向回来的请求，从 URL fragment 中提取 token 并完成登录。
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
  const { completeOAuthSession } = useAuth();
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
      // 从 URL fragment 中提取 token (#access_token=xxx&refresh_token=xxx)
      const hash = window.location.hash.substring(1); // 移除开头的 #
      const params = new URLSearchParams(hash);

      const accessToken = params.get("access_token");
      const refreshToken = params.get("refresh_token");

      // 检查是否有错误参数（从 query 参数中获取）
      const error = searchParams.get("error");

      if (error) {
        navigate("/auth/login?error=oauth_processing_failed", { replace: true });
        return;
      }

      if (!accessToken || !refreshToken) {
        console.error("No tokens found in callback URL");
        navigate("/auth/login?error=oauth_no_token", { replace: true });
        return;
      }

      try {
        const refreshOutcome = await completeOAuthSession(
          accessToken,
          refreshToken,
        );
        if (!ownsCallback()) return;
        if (refreshOutcome.status === "cancelled") return;
        if (refreshOutcome.status !== "completed") {
          throw new Error("auth_hydration_failed");
        }

        // 获取重定向路径
        const redirectPath = getRedirectPath() || "/chat";
        clearRedirectPath();

        // 导航到目标页面
        navigate(redirectPath, { replace: true });
      } catch {
        if (!ownsCallback()) return;
        console.error("[OAuthCallback] OAuth processing failed");
        navigate("/auth/login?error=oauth_processing_failed", {
          replace: true,
        });
      }
    };

    void handleCallback();
    return () => {
      isCurrent = false;
    };
  }, [completeOAuthSession, navigate, searchParams]);

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
