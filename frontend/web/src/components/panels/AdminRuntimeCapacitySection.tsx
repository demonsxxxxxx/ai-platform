import { useCallback, useEffect, useMemo, useState, type ElementType } from "react";
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Database,
  Gauge,
  RefreshCw,
  ShieldCheck,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { useAuth } from "../../hooks/useAuth";
import {
  adminRuntimeApi,
  type AdminRuntimeOverview,
  type RuntimeLimitGroup,
} from "../../services/api/adminRuntime";
import { Permission } from "../../types";
import { shouldFetchAdminRuntimeOverview } from "./adminRuntimeCapacityGuards";

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "enabled" : "disabled";
  }
  if (typeof value === "number") {
    return value.toLocaleString();
  }
  return String(value);
}

function limitValue(
  overview: AdminRuntimeOverview | null,
  group: string,
  key: string,
): string {
  return formatValue(overview?.capacity?.limits?.[group]?.[key]);
}

function firstItems(items: string[] | undefined, limit: number): string[] {
  return (items ?? []).slice(0, limit);
}

function StatusBadge({
  label,
  tone,
}: {
  label: string;
  tone: "neutral" | "warning" | "critical";
}) {
  const toneClass =
    tone === "critical"
      ? "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
      : tone === "warning"
        ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
        : "bg-stone-100 text-stone-600 dark:bg-stone-800 dark:text-stone-300";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${toneClass}`}
    >
      {label}
    </span>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: ElementType;
  label: string;
  value: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 rounded-lg bg-[var(--glass-bg-subtle)] px-3 py-2">
      <Icon size={16} className="shrink-0 text-stone-400 dark:text-stone-500" />
      <div className="min-w-0">
        <p className="text-[11px] text-stone-400 dark:text-stone-500">
          {label}
        </p>
        <p className="truncate text-sm font-medium tabular-nums text-stone-700 dark:text-stone-200">
          {value}
        </p>
      </div>
    </div>
  );
}

function ReasonList({
  title,
  items,
  emptyText,
}: {
  title: string;
  items: string[];
  emptyText: string;
}) {
  return (
    <div>
      <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-stone-400">
        {title}
      </h4>
      {items.length === 0 ? (
        <p className="rounded-lg bg-[var(--glass-bg)] px-3 py-2 text-xs text-stone-500 dark:text-stone-400">
          {emptyText}
        </p>
      ) : (
        <div className="space-y-1">
          {items.map((item) => (
            <div
              key={item}
              className="rounded-md bg-[var(--glass-bg)] px-2.5 py-1.5 text-xs font-medium text-stone-600 dark:text-stone-300"
            >
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function databasePoolSummary(pool?: RuntimeLimitGroup): string {
  const waiting = formatValue(pool?.requests_waiting);
  const maxWaiting = formatValue(pool?.max_waiting);
  return `${waiting} / ${maxWaiting}`;
}

export function AdminRuntimeCapacitySection() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const [overview, setOverview] = useState<AdminRuntimeOverview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const canView = hasPermission(Permission.SETTINGS_MANAGE);

  const fetchOverview = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await adminRuntimeApi.getOverview();
      setOverview(data);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : t("adminRuntime.fetchFailed", "Admin Runtime is unavailable"),
      );
    } finally {
      setIsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (shouldFetchAdminRuntimeOverview(canView)) {
      fetchOverview();
    }
  }, [canView, fetchOverview]);

  const backpressureReasons = overview?.backpressure?.reasons ?? [];
  const capacityWarnings = overview?.capacity?.warnings ?? [];
  const governanceGaps = overview?.governance?.open_gaps ?? [];
  const evidenceGates = overview?.capacity?.load_test_gates ?? [];
  const statusTone = backpressureReasons.length > 0 ? "critical" : "warning";
  const collapsedSummary = useMemo(() => {
    const workerLimit = limitValue(overview, "worker", "max_active_worker_runs");
    const userLimit = limitValue(
      overview,
      "admission",
      "max_active_runs_per_user",
    );
    return t(
      "adminRuntime.summary",
      "Worker runs: {{workerLimit}} · User admission: {{userLimit}} · Evidence: unproven",
      { workerLimit, userLimit },
    );
  }, [overview, t]);

  if (!shouldFetchAdminRuntimeOverview(canView)) return null;

  return (
    <div className="mb-4 rounded-xl border border-[var(--glass-border)] bg-[var(--glass-bg-subtle)]">
      <div
        onClick={() => setExpanded(!expanded)}
        className="flex w-full cursor-pointer items-center justify-between px-4 py-3 select-none"
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setExpanded(!expanded);
          }
        }}
      >
        <div className="flex items-center gap-3">
          <div className="flex size-8 items-center justify-center rounded-lg bg-gradient-to-br from-amber-100 to-stone-50 text-amber-700 dark:from-amber-900/50 dark:to-stone-900/30 dark:text-amber-300">
            <Gauge size={16} />
          </div>
          <div>
            <span className="text-sm font-semibold text-stone-800 dark:text-stone-100">
              {t("adminRuntime.title", "Admin Runtime Capacity")}
            </span>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <StatusBadge
                label={
                  backpressureReasons.length > 0
                    ? t("adminRuntime.backpressure", "Backpressure")
                    : t("adminRuntime.capacityUnproven", "Capacity unproven")
                }
                tone={statusTone}
              />
              {overview?.governance?.status && (
                <StatusBadge
                  label={t("adminRuntime.governance", "Governance")}
                  tone="warning"
                />
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={(event) => {
              event.stopPropagation();
              fetchOverview();
            }}
            disabled={isLoading}
            className="rounded-lg p-1.5 text-stone-400 transition-colors hover:bg-[var(--glass-bg)] hover:text-stone-600 disabled:opacity-50 dark:text-stone-500 dark:hover:text-stone-300"
            aria-label={t("adminRuntime.refresh", "Refresh Admin Runtime")}
          >
            <RefreshCw size={14} className={isLoading ? "animate-spin" : ""} />
          </button>
          {expanded ? (
            <ChevronUp size={16} className="text-stone-400" />
          ) : (
            <ChevronDown size={16} className="text-stone-400" />
          )}
        </div>
      </div>

      {!expanded && (
        <div className="border-t border-[var(--glass-border)] px-4 py-2">
          <p className="text-xs text-stone-500 dark:text-stone-400">
            {collapsedSummary}
          </p>
        </div>
      )}

      {expanded && (
        <div className="border-t border-[var(--glass-border)] px-4 py-3">
          {isLoading && !overview && (
            <div className="flex items-center justify-center py-6">
              <LoadingSpinner size="sm" />
            </div>
          )}

          {error && (
            <p className="py-2 text-center text-sm text-red-500">{error}</p>
          )}

          {overview && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                <MetricCard
                  icon={Activity}
                  label={t("adminRuntime.workerRuns", "Capacity")}
                  value={limitValue(
                    overview,
                    "worker",
                    "max_active_worker_runs",
                  )}
                />
                <MetricCard
                  icon={Users}
                  label={t("adminRuntime.userAdmission", "User admission")}
                  value={limitValue(
                    overview,
                    "admission",
                    "max_active_runs_per_user",
                  )}
                />
                <MetricCard
                  icon={Database}
                  label={t("adminRuntime.dbPool", "DB waiting")}
                  value={databasePoolSummary(overview.backpressure?.database_pool)}
                />
                <MetricCard
                  icon={ShieldCheck}
                  label={t("adminRuntime.governanceLabel", "Governance")}
                  value={formatValue(overview.governance?.status)}
                />
              </div>

              <ReasonList
                title={t("adminRuntime.backpressureTitle", "Backpressure")}
                items={firstItems(backpressureReasons, 5)}
                emptyText={t(
                  "adminRuntime.noBackpressure",
                  "No live backpressure reasons reported.",
                )}
              />

              <ReasonList
                title={t("adminRuntime.capacityWarnings", "Capacity warnings")}
                items={firstItems(capacityWarnings, 5)}
                emptyText={t(
                  "adminRuntime.noCapacityWarnings",
                  "No configured capacity warnings reported.",
                )}
              />

              <ReasonList
                title={t("adminRuntime.governanceTitle", "Governance")}
                items={firstItems(governanceGaps, 5)}
                emptyText={t(
                  "adminRuntime.noGovernanceGaps",
                  "No governance gaps reported by the projection.",
                )}
              />

              <div className="rounded-lg bg-[var(--glass-bg)] px-3 py-2">
                <div className="flex items-start gap-2">
                  <AlertTriangle
                    size={15}
                    className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-300"
                  />
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-stone-700 dark:text-stone-200">
                      {t(
                        "adminRuntime.loadTestEvidence",
                        "Load-test evidence",
                      )}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-stone-500 dark:text-stone-400">
                      {t(
                        "adminRuntime.loadTestEvidenceBody",
                        "{{count}} gates remain required before raising production concurrency defaults.",
                        { count: evidenceGates.length },
                      )}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
