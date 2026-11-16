import { Button } from "@lobehub/ui/base-ui";
import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  AlertTriangle,
  Box,
  CheckCircle,
  ClipboardList,
  Clock,
  Download,
  ExternalLink,
  FileText,
  ListTree,
  PlayCircle,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { Spin } from "@lobehub/ui/base-ui";
import type { CollapsibleStatus } from "../../common";
import { FailureGuidanceCard } from "../../common/FailureGuidanceCard";
import {
  buildRunPlaybackErrorViewModel,
  buildRunPlaybackLoadingViewModel,
  buildRunPlaybackPanelViewModel,
  type RunPlaybackArtifactItem,
  type RunPlaybackContextWindowViewModel,
  type RunPlaybackDisplayStatus,
  type RunPlaybackPanelSummary,
  type RunPlaybackPanelViewModel,
  type RunPlaybackTimelineItem,
} from "./runPlaybackPanelState";
import { downloadRunPlaybackArtifact } from "./runPlaybackDownload";
import {
  buildRunPlaybackArtifactPreviewRequest,
  openRunPlaybackArtifactPreview,
} from "./runPlaybackArtifactPreview";
import { updatePersistentToolPanel } from "../../chat/ChatMessage/items/persistentToolPanelState";
import {
  type RunControlLifecycle,
  type RunControlPhase,
  type RunControlReadError,
  type RunControlSnapshot,
} from "../../../hooks/useAgent/runControlLifecycle";

interface RunPlaybackPanelProps {
  lifecycle: RunControlLifecycle;
  panelKey: string;
}

export function RunPlaybackPanel({ lifecycle, panelKey }: RunPlaybackPanelProps) {
  const snapshot = useSyncExternalStore(
    lifecycle.subscribe,
    lifecycle.getSnapshot,
    lifecycle.getSnapshot,
  );
  const runId =
    snapshot.owner?.runId ??
    snapshot.child?.runId ??
    snapshot.playback?.run_id ??
    "run";
  const viewModel = getViewModel(snapshot, runId);

  useEffect(() => {
    updatePersistentToolPanel(
      (prev) => ({
        ...prev,
        status: getPanelStatus(viewModel),
        subtitle: viewModel.summary.status ?? runId,
      }),
      panelKey,
    );
  }, [panelKey, runId, viewModel]);

  return (
    <div
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg-card)] text-[var(--theme-text)] dark:bg-neutral-900"
      data-run-playback-panel
      data-run-playback-state={viewModel.state}
      aria-busy={snapshot.isBusy}
    >
      <SummaryBlock summary={viewModel.summary} />
      <RunControlActions lifecycle={lifecycle} snapshot={snapshot} />
      {snapshot.playback && (
        <ReadFailureNotice lifecycle={lifecycle} snapshot={snapshot} />
      )}
      {viewModel.failureGuidance ? (
        <div className="shrink-0 border-b border-[var(--theme-border)] px-3 py-3 dark:border-neutral-800 sm:px-4">
          <FailureGuidanceCard guidance={viewModel.failureGuidance} />
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4">
        {viewModel.state === "loading" && <LoadingBlock />}
        {viewModel.state === "error" && (
          <ErrorBlock
            message={viewModel.errorMessage}
            readFailure={snapshot.rejectionMessage ? null : snapshot.playbackReadError}
            readinessFailure={snapshot.readinessReadError}
            onRetry={() => void lifecycle.refresh()}
            disabled={snapshot.isBusy}
          />
        )}
        {viewModel.state === "empty" && <EmptyBlock />}
        {viewModel.state === "ready" && (
          <div className="space-y-4">
            <ContextWindowSection
              contextWindow={viewModel.contextWindow}
            />
            <TimelineSection items={viewModel.timeline} />
            <ArtifactsSection artifacts={viewModel.artifacts} />
          </div>
        )}
      </div>
    </div>
  );
}

function getViewModel(
  snapshot: RunControlSnapshot,
  runId: string,
): RunPlaybackPanelViewModel {
  if (snapshot.playback) {
    return buildRunPlaybackPanelViewModel(snapshot.playback);
  }
  if (snapshot.rejectionMessage) {
    return buildRunPlaybackErrorViewModel(
      runId,
      new Error(snapshot.rejectionMessage),
    );
  }
  if (snapshot.playbackReadError) {
    return buildRunPlaybackErrorViewModel(
      runId,
      new Error(snapshot.playbackReadError.message),
    );
  }
  return buildRunPlaybackLoadingViewModel(runId);
}

/** Safe HTTP metadata already projected at the API boundary, shared by cold and stale reads. */
function ReadErrorDetails({ error }: { error: RunControlReadError | null }) {
  const { t } = useTranslation();
  if (!error) return null;
  const details = [
    error.status !== undefined ? `HTTP ${error.status}` : null,
    error.code ? `${t("runPlayback.readErrorCode")}: ${error.code}` : null,
    error.diagnosticId ? `${t("runPlayback.diagnosticId")}: ${error.diagnosticId}` : null,
  ].filter(Boolean);
  return details.length ? (
    <p className="mt-1 break-words text-xs">{details.join(" · ")}</p>
  ) : null;
}

function ReadFailureNotice({
  lifecycle,
  snapshot,
}: {
  lifecycle: RunControlLifecycle;
  snapshot: RunControlSnapshot;
}) {
  const { t } = useTranslation();
  if (!snapshot.playbackReadError && !snapshot.readinessReadError) return null;
  return (
    <div role="alert" className="shrink-0 border-b border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] px-3 py-2 text-xs text-[var(--theme-warning-text)]">
      {snapshot.playbackReadError && (
        <div>
          <p>{t("runPlayback.stalePlayback")} {snapshot.playbackReadError.message}</p>
          <ReadErrorDetails error={snapshot.playbackReadError} />
        </div>
      )}
      {snapshot.readinessReadError && (
        <div>
          <p>{t("runPlayback.readinessReadFailed")} {snapshot.readinessReadError.message}</p>
          <ReadErrorDetails error={snapshot.readinessReadError} />
        </div>
      )}
      <RunControlButton
        label={t("runPlayback.actions.refresh")}
        disabled={snapshot.isBusy}
        onClick={() => void lifecycle.refresh()}
      />
    </div>
  );
}

function RunControlActions({
  lifecycle,
  snapshot,
}: {
  lifecycle: RunControlLifecycle;
  snapshot: RunControlSnapshot;
}) {
  const { t } = useTranslation();
  const actionMessage = getActionMessage(snapshot, t);
  const disabled = snapshot.isBusy;
  const hasAction =
    snapshot.canCancel ||
    snapshot.canRetry ||
    snapshot.canResume ||
    snapshot.canReconnect ||
    snapshot.phase === "created_unopened" ||
    snapshot.phase === "unconfirmed";

  if (!hasAction && !actionMessage && !snapshot.rejectionMessage) {
    return null;
  }

  return (
    <div className="shrink-0 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-2 dark:border-neutral-800 dark:bg-neutral-950/50 sm:px-4">
      {(actionMessage || snapshot.isBusy) && (
        <div
          role="status"
          aria-live="polite"
          aria-busy={snapshot.isBusy}
          className="text-xs text-neutral-600 dark:text-neutral-300"
        >
          {actionMessage || t("runPlayback.actions.working")}
        </div>
      )}
      {snapshot.rejectionMessage && (
        <div
          role="alert"
          className="mt-1 text-xs text-[var(--theme-danger-text)]"
        >
          {snapshot.rejectionMessage}
        </div>
      )}
      {hasAction && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {snapshot.canCancel && (
            <RunControlButton
              label={t("runPlayback.actions.cancel")}
              disabled={disabled}
              onClick={() => void lifecycle.cancel()}
            />
          )}
          {snapshot.canRetry && (
            <RunControlButton
              label={t("runPlayback.actions.retryRun")}
              disabled={disabled}
              onClick={() => void lifecycle.retry()}
            />
          )}
          {snapshot.canResume && (
            <RunControlButton
              label={t("runPlayback.actions.resume")}
              disabled={disabled}
              onClick={() => void lifecycle.resume()}
            />
          )}
          {snapshot.canReconnect && (
            <RunControlButton
              label={t("runPlayback.actions.reconnect")}
              disabled={disabled}
              onClick={() => void lifecycle.reconnect()}
            />
          )}
          {snapshot.phase === "created_unopened" && (
            <RunControlButton
              label={t("runPlayback.actions.reopen")}
              disabled={disabled}
              onClick={() => void lifecycle.reopenChild()}
            />
          )}
          {snapshot.phase === "unconfirmed" && (
            <RunControlButton
              label={t("runPlayback.actions.refresh")}
              disabled={disabled}
              onClick={() => lifecycle.open()}
            />
          )}
        </div>
      )}
    </div>
  );
}

function RunControlButton({
  label,
  disabled,
  onClick,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <Button type="text"
      htmlType="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex h-8 items-center rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 text-xs font-medium text-neutral-700 transition-colors hover:bg-[var(--theme-bg-sidebar)] disabled:cursor-not-allowed disabled:opacity-60 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200 dark:hover:bg-neutral-800"
    >
      {label}
    </Button>
  );
}

function getActionMessage(snapshot: RunControlSnapshot, t: TFunction): string | null {
  const actionKeys: Partial<Record<RunControlPhase, string>> = {
    cancelling: "cancelling",
    cancel_requested: "cancelRequested",
    retrying: "retrying",
    resuming: "resuming",
    adopting: "adopting",
    created_unopened: "createdUnopened",
    unconfirmed: "unconfirmed",
    reconnecting: "reconnecting",
  };
  const key = actionKeys[snapshot.phase];
  return key ? t(`runPlayback.actionStatus.${key}`) : null;
}

function SummaryBlock({ summary }: { summary: RunPlaybackPanelSummary }) {
  const { t } = useTranslation();
  const fields = [
    { label: t("runPlayback.fields.runId"), value: summary.runId },
    { label: t("runPlayback.fields.status"), value: summary.status },
    { label: t("runPlayback.fields.progress"), value: summary.progressLabel },
    { label: t("runPlayback.fields.traceId"), value: summary.traceId },
    { label: t("runPlayback.fields.sessionId"), value: summary.sessionId },
    { label: t("runPlayback.fields.agentId"), value: summary.agentId },
  ];

  return (
    <div className="shrink-0 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-3 dark:border-neutral-800 dark:bg-neutral-950/50 sm:px-4">
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        {fields.map((field) => (
          <div key={field.label} className="min-w-0">
            <div className="text-[10px] font-medium uppercase text-neutral-400 dark:text-neutral-500">
              {field.label}
            </div>
            <div
              className="mt-0.5 truncate text-xs text-neutral-700 dark:text-neutral-200"
              title={field.value ?? undefined}
            >
              {field.value || "-"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ContextWindowSection({
  contextWindow,
}: {
  contextWindow: RunPlaybackContextWindowViewModel | null;
}) {
  const { t } = useTranslation();
  if (!contextWindow) {
    return null;
  }

  return (
    <PanelSection
      icon={<ClipboardList size={14} />}
      title={t("runPlayback.context.title")}
      count={contextWindow.selectedFileNames.length}
    >
      <div
        data-run-context-window={contextWindow.status}
        className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-neutral-800 dark:bg-neutral-900"
      >
        <div role="status" className="text-xs font-medium text-neutral-700 dark:text-neutral-200">
          {t(`runPlayback.context.status.${contextWindow.status}`)}
        </div>
        {contextWindow.status === "degraded" && (
          <p className="mt-1 text-xs text-neutral-500">
            {t("runPlayback.context.degradedDescription")}
          </p>
        )}
        <dl className="mt-2 flex flex-wrap gap-1.5">
          {contextWindow.historyCounts.map((item) => (
            <div
              key={item.label}
              className="inline-flex items-center gap-1 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] text-neutral-600 dark:border-neutral-800 dark:bg-neutral-950/50 dark:text-neutral-300"
            >
              <dt>{t(`runPlayback.context.counts.${item.label}`)}</dt>
              <dd className="font-semibold tabular-nums">{item.value}</dd>
            </div>
          ))}
        </dl>
        {contextWindow.legacyHistoryExcluded && (
          <p className="mt-2 text-xs text-neutral-500">
            {t("runPlayback.context.legacyHistoryExcluded")}
          </p>
        )}
        {contextWindow.selectedFileNames.length > 0 && (
          <div className="mt-2 min-w-0">
            <div className="text-[10px] font-medium uppercase text-neutral-400 dark:text-neutral-500">
              {t("runPlayback.context.selectedFiles")}
            </div>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {contextWindow.selectedFileNames.map((name) => (
                <li key={name} className="break-all rounded-md bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 text-[11px] text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                  {name}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </PanelSection>
  );
}

function LoadingBlock() {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-2 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-950/50 dark:text-neutral-400">
      <Spin size={16} />
      <span>{t("runPlayback.loading")}</span>
    </div>
  );
}

function ErrorBlock({
  message,
  readFailure,
  readinessFailure,
  onRetry,
  disabled,
}: {
  message: string | null;
  readFailure: RunControlReadError | null;
  readinessFailure: RunControlReadError | null;
  onRetry: () => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div role="alert" className="rounded-md border border-[var(--theme-danger-ring)] bg-[var(--theme-danger-soft)] p-3">
      <div className="flex items-start gap-2">
        <AlertTriangle
          size={16}
          className="mt-0.5 shrink-0 text-[var(--theme-danger-text)]"
        />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-[var(--theme-danger-text)]">
            {t("runPlayback.error")}
          </div>
          <div className="mt-1 text-xs text-[var(--theme-danger-text)]">
            {message || t("runPlayback.errorFallback")}
          </div>
          <ReadErrorDetails error={readFailure} />
          {readinessFailure && (
            <div className="mt-2 text-xs">
              <p>{t("runPlayback.readinessReadFailed")} {readinessFailure.message}</p>
              <ReadErrorDetails error={readinessFailure} />
            </div>
          )}
        </div>
      </div>
      <Button type="text"
        htmlType="button"
        onClick={onRetry}
        disabled={disabled}
        className="mt-3 inline-flex h-8 items-center gap-2 rounded-md border border-[var(--theme-danger-ring)] bg-[var(--theme-bg-card)] px-2.5 text-xs font-medium text-[var(--theme-danger-text)] transition-colors hover:bg-[var(--theme-danger-soft)]"
      >
        <RefreshCw size={13} />
        {t("runPlayback.actions.refresh")}
      </Button>
    </div>
  );
}

function EmptyBlock() {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-4 text-center text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-950/50 dark:text-neutral-400">
      {t("runPlayback.empty")}
    </div>
  );
}

function TimelineSection({ items }: { items: RunPlaybackTimelineItem[] }) {
  const { t } = useTranslation();
  return (
    <PanelSection
      icon={<ListTree size={14} />}
      title={t("runPlayback.timeline")}
      count={items.length}
    >
      {items.length === 0 ? (
        <SectionEmpty label={t("runPlayback.noTimeline")} />
      ) : (
        <div className="space-y-1.5">
          {items.map((item) => (
            <TimelineRow key={item.id} item={item} />
          ))}
        </div>
      )}
    </PanelSection>
  );
}

function TimelineRow({ item }: { item: RunPlaybackTimelineItem }) {
  return (
    <div className="flex gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-neutral-800 dark:bg-neutral-900">
      <StatusIcon status={item.status} kind={item.kind} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-medium text-neutral-800 dark:text-neutral-100">
            {item.label}
          </span>
          <StatusBadge status={item.status} />
        </div>
        <div className="mt-1 flex min-w-0 flex-wrap gap-x-2 gap-y-1 text-[11px] text-neutral-500 dark:text-neutral-500">
          {item.sequence !== null && <span>#{item.sequence}</span>}
          <span>{item.kind}</span>
          {item.detail && <span className="truncate">{item.detail}</span>}
          {item.createdAt && <span className="truncate">{item.createdAt}</span>}
        </div>
      </div>
    </div>
  );
}

function ArtifactsSection({
  artifacts,
}: {
  artifacts: RunPlaybackArtifactItem[];
}) {
  const { t } = useTranslation();
  return (
    <PanelSection
      icon={<Box size={14} />}
      title={t("runPlayback.artifacts")}
      count={artifacts.length}
    >
      {artifacts.length === 0 ? (
        <SectionEmpty label={t("runPlayback.noArtifacts")} />
      ) : (
        <div className="space-y-1.5">
          {artifacts.map((artifact) => (
            <ArtifactRow key={artifact.id} artifact={artifact} />
          ))}
        </div>
      )}
    </PanelSection>
  );
}

function ArtifactRow({ artifact }: { artifact: RunPlaybackArtifactItem }) {
  const { t } = useTranslation();
  const handleDownload = () => {
    void downloadRunPlaybackArtifact(artifact).catch((error) => {
      console.warn("[RunPlaybackPanel] Artifact download failed:", error);
    });
  };
  const previewRequest = buildRunPlaybackArtifactPreviewRequest(artifact);
  const handlePreview = () => {
    openRunPlaybackArtifactPreview(artifact);
  };

  return (
    <div className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex min-w-0 items-start gap-2">
        <FileText
          size={15}
          className="mt-0.5 shrink-0 text-neutral-400 dark:text-neutral-500"
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-xs font-medium text-neutral-800 dark:text-neutral-100">
              {artifact.label}
            </span>
            <StatusBadge status={artifact.status} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-neutral-500 dark:text-neutral-500">
            {artifact.type && <span>{artifact.type}</span>}
            {artifact.contentType && <span>{artifact.contentType}</span>}
            {artifact.sizeLabel && <span>{artifact.sizeLabel}</span>}
          </div>
        </div>
      </div>
      {(artifact.downloadUrl || previewRequest) && (
        <div className="mt-2 flex flex-wrap gap-1.5 pl-6">
          {previewRequest && (
            <Button type="text"
              htmlType="button"
              onClick={handlePreview}
              aria-label={`${t("runPlayback.preview")} ${artifact.label}`}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-[11px] font-medium text-neutral-600 transition-colors hover:bg-[var(--theme-bg-sidebar)] dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              <ExternalLink size={12} />
              {t("runPlayback.preview")}
            </Button>
          )}
          {artifact.downloadUrl && (
            <Button type="text"
              htmlType="button"
              onClick={handleDownload}
              aria-label={`${t("runPlayback.download")} ${artifact.label}`}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-[11px] font-medium text-neutral-600 transition-colors hover:bg-[var(--theme-bg-sidebar)] dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              <Download size={12} />
              {t("runPlayback.download")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function PanelSection({
  icon,
  title,
  count,
  children,
}: {
  icon: ReactNode;
  title: string;
  count: number;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-neutral-700 dark:text-neutral-200">
        <span className="text-neutral-400 dark:text-neutral-500">{icon}</span>
        <span>{title}</span>
        <span className="ml-auto rounded-md bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
          {count}
        </span>
      </div>
      {children}
    </section>
  );
}

function SectionEmpty({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-dashed border-neutral-200 px-3 py-2 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-500">
      {label}
    </div>
  );
}

function StatusBadge({ status }: { status: RunPlaybackDisplayStatus }) {
  const { t } = useTranslation();
  return (
    <span
      className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${getStatusBadgeClass(
        status,
      )}`}
    >
      {translateStatus(t, status)}
    </span>
  );
}

function StatusIcon({
  status,
  kind,
}: {
  status: RunPlaybackDisplayStatus;
  kind: RunPlaybackTimelineItem["kind"];
}) {
  const className = getStatusIconClass(status);
  if (status === "running" || status === "loading") {
    return <Spin size={12} className="mt-0.5 shrink-0" />;
  }
  if (status === "success") {
    return <CheckCircle size={14} className={`mt-0.5 shrink-0 ${className}`} />;
  }
  if (status === "error") {
    return <XCircle size={14} className={`mt-0.5 shrink-0 ${className}`} />;
  }
  if (kind === "artifact") {
    return <FileText size={14} className={`mt-0.5 shrink-0 ${className}`} />;
  }
  if (kind === "step") {
    return <PlayCircle size={14} className={`mt-0.5 shrink-0 ${className}`} />;
  }
  return <Clock size={14} className={`mt-0.5 shrink-0 ${className}`} />;
}

function getPanelStatus(
  viewModel: RunPlaybackPanelViewModel,
): CollapsibleStatus {
  if (viewModel.state === "loading") return "loading";
  if (viewModel.state === "error") return "error";
  const status = viewModel.summary.status?.toLowerCase() ?? "";
  if (status.includes("fail") || status.includes("error")) return "error";
  if (status.includes("cancel")) return "cancelled";
  if (
    status.includes("running") ||
    status.includes("pending") ||
    status.includes("queued")
  ) {
    return "loading";
  }
  if (
    status.includes("complete") ||
    status.includes("success") ||
    status.includes("succeed")
  ) {
    return "success";
  }
  return "idle";
}

function getStatusBadgeClass(status: RunPlaybackDisplayStatus): string {
  if (status === "success") {
    return "bg-[var(--theme-success-soft)] text-[var(--theme-success-text)]";
  }
  if (status === "error") {
    return "bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)]";
  }
  if (status === "running" || status === "loading") {
    return "bg-[var(--theme-warning-soft)] text-[var(--theme-warning-text)]";
  }
  if (status === "cancelled" || status === "blocked") {
    return "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300";
  }
  return "bg-[var(--theme-bg-sidebar)] text-neutral-500 dark:bg-neutral-900 dark:text-neutral-400";
}

function getStatusIconClass(status: RunPlaybackDisplayStatus): string {
  if (status === "success") return "text-[var(--theme-success-text)]";
  if (status === "error") return "text-[var(--theme-danger-text)]";
  if (status === "cancelled" || status === "blocked") {
    return "text-neutral-500 dark:text-neutral-400";
  }
  return "text-neutral-400 dark:text-neutral-500";
}

function translateStatus(t: TFunction, status: string): string {
  return t(`runPlayback.status.${status}`, { defaultValue: status });
}
