/**
 * Dormant OAuth callback compatibility component.
 *
 * App.tsx routes Phase 1 OAuth callbacks to a fail-closed page. This file is
 * kept only so old import paths fail safely if reintroduced accidentally.
 */

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Loading } from "../common";

export function OAuthCallback() {
  const navigate = useNavigate();

  useEffect(() => {
    navigate("/auth/login?error=oauth_phase2_unavailable", { replace: true });
  }, [navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-stone-50 dark:bg-stone-900">
      <Loading size="lg" className="justify-center" />
    </div>
  );
}

export default OAuthCallback;
