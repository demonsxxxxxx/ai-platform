import { RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../hooks/useAuth";
import {
  decideToolPermissionInbox,
  listToolPermissionInbox,
  type ToolPermissionDecision,
  type ToolPermissionInboxResponse,
  type ToolPermissionRequestView,
} from "../../services/api/toolPermission";

export interface AdminToolPermissionInboxClient {
  list: () => Promise<ToolPermissionInboxResponse>;
  decide: (
    requestId: string,
    decision: ToolPermissionDecision,
  ) => Promise<unknown>;
}

const defaultClient: AdminToolPermissionInboxClient = {
  list: () => listToolPermissionInbox("pending"),
  decide: (requestId, decision) =>
    decideToolPermissionInbox(requestId, decision),
};

function inboxErrorKey(error: unknown): string {
  const status =
    error && typeof error === "object" && "status" in error
      ? (error as { status?: unknown }).status
      : undefined;
  if (status === 403) return "settings.toolPermissionInbox.forbidden";
  if (status === 409) return "settings.toolPermissionInbox.alreadyDecided";
  return "settings.toolPermissionInbox.requestFailed";
}

function RequestSummary({ request }: { request: ToolPermissionRequestView }) {
  const { t } = useTranslation();
  return (
    <div className="min-w-0">
      <p className="truncate text-sm font-medium text-stone-700 dark:text-stone-200">
        {request.tool_id}
      </p>
      <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
        {t("settings.toolPermissionInbox.risk", {
          level: t(`chat.toolPermission.riskLevels.${request.risk_level}`, {
            defaultValue: request.risk_level,
          }),
        })}
        {" · "}
        {request.write_capable
          ? t("chat.toolPermission.writeCapable")
          : t("chat.toolPermission.readOnly")}
      </p>
    </div>
  );
}

/** Tenant-wide, administrator-only inbox for governed tool decisions. */
export function AdminToolPermissionInboxSection({
  client = defaultClient,
}: {
  client?: AdminToolPermissionInboxClient;
}) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.is_admin === true;
  const [requests, setRequests] = useState<ToolPermissionRequestView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setErrorKey(null);
    try {
      const response = await client.list();
      setRequests(response.permission_requests);
    } catch (error) {
      setErrorKey(inboxErrorKey(error));
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (isAdmin) {
      void refresh();
    }
  }, [isAdmin, refresh]);

  const decide = useCallback(
    async (requestId: string, decision: ToolPermissionDecision) => {
      setDecidingId(requestId);
      setErrorKey(null);
      try {
        await client.decide(requestId, decision);
        setRequests((previous) =>
          previous.filter(
            (request) => request.permission_request_id !== requestId,
          ),
        );
        // A refresh makes a concurrent/duplicate server decision converge
        // without relying on the owner-scoped chat session endpoint.
        await refresh();
      } catch (error) {
        setErrorKey(inboxErrorKey(error));
      } finally {
        setDecidingId(null);
      }
    },
    [client, refresh],
  );

  // This strict projection is the only frontend authorization gate.  It
  // ensures ordinary users do not see or fetch the tenant governance inbox.
  if (!isAdmin) return null;

  return (
    <section className="panel-card mb-4 p-0" aria-label={t("settings.toolPermissionInbox.title")}>
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-primary)] ring-1 ring-[var(--theme-border)]">
            <ShieldCheck size={16} />
          </div>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-stone-800 dark:text-stone-100">
              {t("settings.toolPermissionInbox.title")}
            </h3>
            <p className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">
              {t("settings.toolPermissionInbox.description")}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={isLoading || decidingId !== null}
          className="enterprise-icon-button disabled:opacity-50"
          aria-label={t("settings.toolPermissionInbox.refresh")}
        >
          <RefreshCw size={14} className={isLoading ? "animate-spin" : ""} />
        </button>
      </div>

      <div className="space-y-2 border-t border-[var(--theme-border)] px-4 py-3">
        {errorKey && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-900/30 dark:text-red-300">
            {t(errorKey)}
          </p>
        )}
        {!isLoading && requests.length === 0 && !errorKey && (
          <p className="text-xs text-stone-500 dark:text-stone-400">
            {t("settings.toolPermissionInbox.empty")}
          </p>
        )}
        {requests.map((request) => (
          <div
            key={request.permission_request_id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-[var(--theme-bg-sidebar)] px-3 py-2"
          >
            <RequestSummary request={request} />
            <div className="flex flex-wrap gap-1.5">
              {(["allow_once", "allow_for_run", "deny"] as const).map(
                (decision) => (
                  <button
                    key={decision}
                    type="button"
                    onClick={() => void decide(request.permission_request_id, decision)}
                    disabled={decidingId !== null}
                    className={
                      decision === "deny"
                        ? "rounded-md border border-red-200 px-2 py-1 text-xs font-medium text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-300"
                        : "rounded-md border border-[var(--theme-border)] px-2 py-1 text-xs font-medium text-stone-700 disabled:opacity-50 dark:text-stone-200"
                    }
                  >
                    {t(`chat.toolPermission.decisions.${decision}`)}
                  </button>
                ),
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
