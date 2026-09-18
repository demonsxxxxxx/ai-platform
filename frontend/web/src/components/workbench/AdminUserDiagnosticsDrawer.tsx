import { ExternalLink, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  AdminUserDiagnosticsResponse,
  AdminUserSummary,
} from "../../services/api/adminUsers";
import { formatDateTimeShort } from "../../utils/datetime";


export function AdminUserDiagnosticsDrawer({
  user,
  diagnostics,
  error,
  loading,
  onClose,
}: {
  user: AdminUserSummary;
  diagnostics: AdminUserDiagnosticsResponse | null;
  error: string | null;
  loading: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-[300]">
      <button
        type="button"
        className="absolute inset-0 cursor-default bg-[var(--theme-overlay-strong)]"
        aria-label={t("workbench.projections.users.closeDiagnostics")}
        onClick={onClose}
      />
      <aside
        data-admin-user-diagnostics={user.user_id}
        role="dialog"
        aria-modal="true"
        aria-label={t("workbench.projections.users.auditDebug")}
        className="absolute inset-y-0 right-0 w-full overflow-y-auto bg-[var(--theme-workbench-panel)] shadow-xl md:w-[46rem]"
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-[var(--theme-text)]">
              {user.display_name || user.user_id}
            </h2>
            <p className="mt-1 font-mono text-xs text-[var(--theme-text-secondary)]">
              {user.user_id}
            </p>
          </div>
          <button
            type="button"
            className="btn-icon"
            aria-label={t("workbench.projections.users.closeDiagnostics")}
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="p-5 text-sm text-[var(--theme-text-secondary)]">
            {t("workbench.projections.users.diagnosticsLoading")}
          </div>
        ) : error ? (
          <div className="m-5 border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-sm text-[var(--theme-danger)]">
            {error}
          </div>
        ) : diagnostics ? (
          <div className="space-y-5 p-5">
            <section>
              <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                {t("workbench.projections.users.recentRuns")}
              </h3>
              <div className="mt-2 divide-y divide-[var(--theme-border)] rounded-md border border-[var(--theme-border)]">
                {diagnostics.runs.length ? (
                  diagnostics.runs.map((run) => (
                    <div
                      key={run.run_id}
                      className="flex items-start justify-between gap-3 px-3 py-2.5 text-xs"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-mono font-medium text-[var(--theme-text)]">
                          {run.run_id}
                        </p>
                        <p className="mt-1 text-[var(--theme-text-secondary)]">
                          {run.status} · {run.agent_id}
                          {run.error_code ? ` · ${run.error_code}` : ""}
                        </p>
                      </div>
                      <a
                        className="inline-flex shrink-0 items-center gap-1 font-medium text-[var(--theme-primary)] hover:underline"
                        href={`/runs?user_id=${encodeURIComponent(user.user_id)}&run_id=${encodeURIComponent(run.run_id)}`}
                      >
                        {t("workbench.projections.users.openRun")}
                        <ExternalLink size={12} />
                      </a>
                    </div>
                  ))
                ) : (
                  <p className="px-3 py-4 text-xs text-[var(--theme-text-tertiary)]">
                    {t("workbench.projections.users.noRuns")}
                  </p>
                )}
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                {t("workbench.projections.users.recentSessions")}
              </h3>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {diagnostics.sessions.length ? (
                  diagnostics.sessions.map((session) => (
                    <div
                      key={session.session_id}
                      className="rounded-md border border-[var(--theme-border)] p-3 text-xs"
                    >
                      <p className="truncate font-mono font-medium text-[var(--theme-text)]">
                        {session.session_id}
                      </p>
                      <p className="mt-1 text-[var(--theme-text-secondary)]">
                        {session.agent_id} · {session.run_count} Runs · {session.failed_run_count}{" "}
                        {t("workbench.projections.users.failedShort")}
                      </p>
                    </div>
                  ))
                ) : (
                  <p className="rounded-md border border-[var(--theme-border)] px-3 py-4 text-xs text-[var(--theme-text-tertiary)] sm:col-span-2">
                    {t("workbench.projections.users.noSessions")}
                  </p>
                )}
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold text-[var(--theme-text)]">
                {t("workbench.projections.users.auditTimeline")}
              </h3>
              <p className="mt-1 text-xs text-[var(--theme-text-tertiary)]">
                {t("workbench.projections.users.auditBoundary")}
              </p>
              <div className="mt-2 space-y-2">
                {diagnostics.audit.length ? (
                  diagnostics.audit.map((entry) => (
                    <div
                      key={entry.audit_id}
                      className="rounded-md border border-[var(--theme-border)] px-3 py-2.5 text-xs"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium text-[var(--theme-text)]">
                          {entry.action}
                        </span>
                        <span className="text-[var(--theme-text-tertiary)]">
                          {entry.created_at ? formatDateTimeShort(entry.created_at) : "-"}
                        </span>
                      </div>
                      <p className="mt-1 text-[var(--theme-text-secondary)]">
                        {entry.relations.join(" / ")} · {entry.target_type}:{entry.target_id}
                      </p>
                    </div>
                  ))
                ) : (
                  <p className="rounded-md border border-[var(--theme-border)] px-3 py-4 text-xs text-[var(--theme-text-tertiary)]">
                    {t("workbench.projections.users.noAudit")}
                  </p>
                )}
              </div>
            </section>
          </div>
        ) : null}
      </aside>
    </div>
  );
}
