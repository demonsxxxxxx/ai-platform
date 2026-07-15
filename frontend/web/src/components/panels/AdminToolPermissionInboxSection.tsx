import { RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../hooks/useAuth";
import {
  decideToolPermissionInbox,
  listToolPermissionInbox,
  type ToolPermissionDecision,
  type ToolPermissionInboxRequestView,
  type ToolPermissionInboxResponse,
} from "../../services/api/toolPermission";
import { ApiRequestError } from "../../services/api/fetch";
import { Permission } from "../../types";
import {
  governanceInboxSubjectKey,
  isInboxDecisionDisabled,
} from "./adminToolPermissionInboxState";

export interface AdminToolPermissionInboxClient {
  list: (signal?: AbortSignal) => Promise<ToolPermissionInboxResponse>;
  decide: (
    requestId: string,
    decision: ToolPermissionDecision,
    signal?: AbortSignal,
  ) => Promise<unknown>;
}

const defaultClient: AdminToolPermissionInboxClient = {
  list: (signal) => listToolPermissionInbox("pending", { signal }),
  decide: (requestId, decision, signal) =>
    decideToolPermissionInbox(requestId, decision, undefined, { signal }),
};

interface InboxOwnedState {
  subjectKey: string | null;
  requests: ToolPermissionInboxRequestView[];
  isLoading: boolean;
  decidingId: string | null;
  errorKey: string | null;
  authorizationDenied: boolean;
}

function emptyInboxState(subjectKey: string | null): InboxOwnedState {
  return {
    subjectKey,
    requests: [],
    isLoading: false,
    decidingId: null,
    errorKey: null,
    authorizationDenied: false,
  };
}

function isAlreadyDecidedConflict(error: unknown): boolean {
  return (
    error instanceof ApiRequestError &&
    error.code === "tool_permission_request_not_pending"
  );
}

function isAuthorizationDeniedError(error: unknown): error is ApiRequestError {
  return (
    error instanceof ApiRequestError &&
    (error.status === 401 || error.status === 403)
  );
}

function inboxErrorKey(error: unknown): string {
  if (!(error instanceof ApiRequestError)) {
    return "settings.toolPermissionInbox.requestFailed";
  }
  if (error.status === 401 || error.status === 403) {
    return "settings.toolPermissionInbox.forbidden";
  }
  if (error.code === "tool_permission_request_not_pending") {
    return "settings.toolPermissionInbox.alreadyDecided";
  }
  if (error.code === "tool_permission_decision_not_supported") {
    return "settings.toolPermissionInbox.decisionNotSupported";
  }
  return "settings.toolPermissionInbox.requestFailed";
}

function RequestSummary({ request }: { request: ToolPermissionInboxRequestView }) {
  const { t } = useTranslation();
  return (
    <div className="min-w-0">
      <p className="truncate text-sm font-medium text-stone-700 dark:text-stone-200">
        {request.tool_display || request.tool_id}
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
  const { user, hasPermission } = useAuth();
  const canGovern =
    user?.is_admin === true && hasPermission(Permission.SETTINGS_MANAGE);
  const subjectKey = governanceInboxSubjectKey(user, canGovern);
  const [ownedState, setOwnedState] = useState<InboxOwnedState>(() =>
    emptyInboxState(null),
  );
  const currentSubjectKeyRef = useRef<string | null>(subjectKey);
  currentSubjectKeyRef.current = subjectKey;
  const subjectGenerationRef = useRef(0);
  const refreshGenerationRef = useRef(0);
  const decisionGenerationRef = useRef(0);
  const refreshAbortControllerRef = useRef<AbortController | null>(null);
  const decisionAbortControllerRef = useRef<AbortController | null>(null);
  const decidedRequestIdsRef = useRef(new Set<string>());

  const abortOwnedWork = useCallback(() => {
    refreshGenerationRef.current += 1;
    decisionGenerationRef.current += 1;
    refreshAbortControllerRef.current?.abort();
    refreshAbortControllerRef.current = null;
    decisionAbortControllerRef.current?.abort();
    decisionAbortControllerRef.current = null;
  }, []);

  const isCurrentSubject = useCallback(
    (targetSubjectKey: string, targetSubjectGeneration: number) =>
      currentSubjectKeyRef.current === targetSubjectKey &&
      subjectGenerationRef.current === targetSubjectGeneration,
    [],
  );

  const revokeOwnedSubject = useCallback((
    targetSubjectKey: string,
    targetSubjectGeneration: number,
  ): boolean => {
    if (!isCurrentSubject(targetSubjectKey, targetSubjectGeneration)) {
      return false;
    }
    subjectGenerationRef.current += 1;
    abortOwnedWork();
    decidedRequestIdsRef.current.clear();
    setOwnedState({
      ...emptyInboxState(targetSubjectKey),
      errorKey: "settings.toolPermissionInbox.forbidden",
      authorizationDenied: true,
    });
    return true;
  }, [abortOwnedWork, isCurrentSubject]);

  const refreshOwnedSubject = useCallback(async (
    targetSubjectKey: string,
    targetSubjectGeneration: number,
  ) => {
    if (!isCurrentSubject(targetSubjectKey, targetSubjectGeneration)) return;
    const refreshGeneration = refreshGenerationRef.current + 1;
    refreshGenerationRef.current = refreshGeneration;
    refreshAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    refreshAbortControllerRef.current = abortController;
    const isCurrentRefresh = () =>
      isCurrentSubject(targetSubjectKey, targetSubjectGeneration) &&
      refreshGenerationRef.current === refreshGeneration &&
      refreshAbortControllerRef.current === abortController &&
      !abortController.signal.aborted;
    setOwnedState((previous) =>
      previous.subjectKey === targetSubjectKey
        ? { ...previous, isLoading: true, errorKey: null }
        : { ...emptyInboxState(targetSubjectKey), isLoading: true },
    );
    try {
      const response = await client.list(abortController.signal);
      if (!isCurrentRefresh()) return;
      setOwnedState((previous) =>
        previous.subjectKey === targetSubjectKey
          ? {
              ...previous,
              requests: response.permission_requests.filter(
                (request) =>
                  !decidedRequestIdsRef.current.has(request.request_id),
              ),
            }
          : previous,
      );
    } catch (error) {
      if (!isCurrentRefresh()) return;
      if (isAuthorizationDeniedError(error)) {
        revokeOwnedSubject(targetSubjectKey, targetSubjectGeneration);
        return;
      }
      setOwnedState((previous) =>
        previous.subjectKey === targetSubjectKey
          ? { ...previous, errorKey: inboxErrorKey(error) }
          : previous,
      );
    } finally {
      if (isCurrentRefresh()) {
        refreshAbortControllerRef.current = null;
        setOwnedState((previous) =>
          previous.subjectKey === targetSubjectKey
            ? { ...previous, isLoading: false }
            : previous,
        );
      }
    }
  }, [client, isCurrentSubject, revokeOwnedSubject]);

  useEffect(() => {
    abortOwnedWork();
    subjectGenerationRef.current += 1;
    const subjectGeneration = subjectGenerationRef.current;
    decidedRequestIdsRef.current.clear();
    setOwnedState(emptyInboxState(subjectKey));
    if (subjectKey) {
      void refreshOwnedSubject(subjectKey, subjectGeneration);
    }
    return () => {
      if (subjectGenerationRef.current === subjectGeneration) {
        subjectGenerationRef.current += 1;
      }
      abortOwnedWork();
    };
  }, [abortOwnedWork, refreshOwnedSubject, subjectKey]);

  const stateBelongsToSubject = ownedState.subjectKey === subjectKey;
  const requests = stateBelongsToSubject ? ownedState.requests : [];
  const isLoading = stateBelongsToSubject
    ? ownedState.isLoading
    : subjectKey !== null;
  const decidingId = stateBelongsToSubject ? ownedState.decidingId : null;
  const errorKey = stateBelongsToSubject ? ownedState.errorKey : null;
  const authorizationDenied = stateBelongsToSubject
    ? ownedState.authorizationDenied
    : false;

  const decide = useCallback(
    async (requestId: string, decision: ToolPermissionDecision) => {
      const targetSubjectKey = currentSubjectKeyRef.current;
      const targetSubjectGeneration = subjectGenerationRef.current;
      if (!targetSubjectKey) return;
      if (authorizationDenied) return;
      if (isInboxDecisionDisabled(isLoading, decidingId)) return;
      const decisionGeneration = decisionGenerationRef.current + 1;
      decisionGenerationRef.current = decisionGeneration;
      decisionAbortControllerRef.current?.abort();
      const abortController = new AbortController();
      decisionAbortControllerRef.current = abortController;
      const isCurrentDecision = () =>
        isCurrentSubject(targetSubjectKey, targetSubjectGeneration) &&
        decisionGenerationRef.current === decisionGeneration &&
        decisionAbortControllerRef.current === abortController &&
        !abortController.signal.aborted;
      setOwnedState((previous) =>
        previous.subjectKey === targetSubjectKey
          ? { ...previous, decidingId: requestId, errorKey: null }
          : previous,
      );
      try {
        await client.decide(requestId, decision, abortController.signal);
        if (!isCurrentDecision()) return;
        decidedRequestIdsRef.current.add(requestId);
        setOwnedState((previous) =>
          previous.subjectKey === targetSubjectKey
            ? {
                ...previous,
                requests: previous.requests.filter(
                  (request) => request.request_id !== requestId,
                ),
              }
            : previous,
        );
        // A refresh makes a concurrent/duplicate server decision converge
        // without relying on the owner-scoped chat session endpoint.
        await refreshOwnedSubject(targetSubjectKey, targetSubjectGeneration);
      } catch (error) {
        if (!isCurrentDecision()) return;
        if (isAuthorizationDeniedError(error)) {
          revokeOwnedSubject(targetSubjectKey, targetSubjectGeneration);
          return;
        }
        if (isAlreadyDecidedConflict(error)) {
          decidedRequestIdsRef.current.add(requestId);
          setOwnedState((previous) =>
            previous.subjectKey === targetSubjectKey
              ? {
                  ...previous,
                  requests: previous.requests.filter(
                    (request) => request.request_id !== requestId,
                  ),
                  errorKey: null,
                }
              : previous,
          );
          await refreshOwnedSubject(targetSubjectKey, targetSubjectGeneration);
          return;
        }
        setOwnedState((previous) =>
          previous.subjectKey === targetSubjectKey
            ? { ...previous, errorKey: inboxErrorKey(error) }
            : previous,
        );
      } finally {
        if (isCurrentDecision()) {
          decisionAbortControllerRef.current = null;
          setOwnedState((previous) =>
            previous.subjectKey === targetSubjectKey
              ? { ...previous, decidingId: null }
              : previous,
          );
        }
      }
    },
    [
      authorizationDenied,
      client,
      decidingId,
      isCurrentSubject,
      isLoading,
      refreshOwnedSubject,
      revokeOwnedSubject,
    ],
  );

  // This strict projection is the only frontend authorization gate.  It
  // ensures ordinary users do not see or fetch the tenant governance inbox.
  if (!subjectKey) return null;

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
          onClick={() =>
            !authorizationDenied &&
            void refreshOwnedSubject(subjectKey, subjectGenerationRef.current)
          }
          disabled={authorizationDenied || isLoading || decidingId !== null}
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
            key={request.request_id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-[var(--theme-bg-sidebar)] px-3 py-2"
          >
            <RequestSummary request={request} />
            <div className="flex flex-wrap gap-1.5">
              {request.allowed_decisions.map(
                (decision) => (
                  <button
                    key={decision}
                    type="button"
                    onClick={() => void decide(request.request_id, decision)}
                    disabled={isInboxDecisionDisabled(isLoading, decidingId)}
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
