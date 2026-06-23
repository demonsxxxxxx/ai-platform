import { useEffect, useState, type ReactNode } from "react";
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
  Users,
  XCircle,
} from "lucide-react";
import { LoadingSpinner } from "../../common";
import type { CollapsibleStatus } from "../../common";
import { fetchRunPlayback } from "../../../services/api/runPlayback";
import {
  buildRunPlaybackErrorViewModel,
  buildRunPlaybackLoadingViewModel,
  buildRunPlaybackPanelViewModel,
  type RunPlaybackArtifactItem,
  type RunPlaybackContextProvenanceViewModel,
  type RunPlaybackDisplayStatus,
  type RunPlaybackPanelSummary,
  type RunPlaybackPanelViewModel,
  type RunPlaybackStepItem,
  type RunPlaybackTimelineItem,
} from "./runPlaybackPanelState";
import { downloadRunPlaybackArtifact } from "./runPlaybackDownload";
import {
  buildRunPlaybackArtifactPreviewRequest,
  openRunPlaybackArtifactPreview,
} from "./runPlaybackArtifactPreview";
import { updatePersistentToolPanel } from "../../chat/ChatMessage/items/persistentToolPanelState";

interface RunPlaybackPanelProps {
  runId: string;
  panelKey: string;
}

export function RunPlaybackPanel({ runId, panelKey }: RunPlaybackPanelProps) {
  const [reloadToken, setReloadToken] = useState(0);
  const [viewModel, setViewModel] = useState<RunPlaybackPanelViewModel>(() =>
    buildRunPlaybackLoadingViewModel(runId),
  );

  useEffect(() => {
    let cancelled = false;
    setViewModel(buildRunPlaybackLoadingViewModel(runId));

    fetchRunPlayback(runId)
      .then((response) => {
        if (!cancelled) {
          setViewModel(buildRunPlaybackPanelViewModel(response));
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setViewModel(buildRunPlaybackErrorViewModel(runId, error));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [runId, reloadToken]);

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

  const handleRetry = () => {
    setReloadToken((value) => value + 1);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--theme-bg-card)] text-[var(--theme-text)] dark:bg-stone-900">
      <SummaryBlock summary={viewModel.summary} />

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4">
        {viewModel.state === "loading" && <LoadingBlock />}
        {viewModel.state === "error" && (
          <ErrorBlock
            message={viewModel.errorMessage}
            onRetry={handleRetry}
          />
        )}
        {viewModel.state === "empty" && <EmptyBlock />}
        {viewModel.state === "ready" && (
          <div className="space-y-4">
            <ContextProvenanceSection
              contextProvenance={viewModel.contextProvenance}
            />
            <TimelineSection items={viewModel.timeline} />
            <ArtifactsSection artifacts={viewModel.artifacts} />
            <MultiAgentSection
              counts={viewModel.multiAgent.counts}
              steps={viewModel.multiAgent.steps}
            />
          </div>
        )}
      </div>
    </div>
  );
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
    <div className="shrink-0 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-3 dark:border-stone-800 dark:bg-stone-950/50 sm:px-4">
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        {fields.map((field) => (
          <div key={field.label} className="min-w-0">
            <div className="text-[10px] font-medium uppercase text-stone-400 dark:text-stone-500">
              {field.label}
            </div>
            <div
              className="mt-0.5 truncate text-xs text-stone-700 dark:text-stone-200"
              title={field.value ?? undefined}
            >
              {field.value || "-"}
            </div>
          </div>
        ))}
      </div>
      {summary.errorMessage && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-xs text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
          {summary.errorMessage}
        </div>
      )}
    </div>
  );
}

function ContextProvenanceSection({
  contextProvenance,
}: {
  contextProvenance: RunPlaybackContextProvenanceViewModel | null;
}) {
  const { t } = useTranslation();
  if (!contextProvenance) {
    return null;
  }

  const detailFields = [
    {
      label: t("runPlayback.context.executionTier"),
      value: contextProvenance.executionTier,
    },
    {
      label: t("runPlayback.context.contextPackVersion"),
      value: contextProvenance.contextPackVersion,
    },
    {
      label: t("runPlayback.context.generatedAt"),
      value: contextProvenance.contextPackGeneratedAt,
    },
    {
      label: t("runPlayback.context.latestArtifactVersion"),
      value: contextProvenance.latestArtifactVersion,
    },
    {
      label: t("runPlayback.context.source"),
      value: contextProvenance.source,
    },
  ].filter((field) => field.value);

  return (
    <PanelSection
      icon={<ClipboardList size={14} />}
      title={t("runPlayback.context.title")}
      count={
        contextProvenance.referencedMaterials.length +
        contextProvenance.inputKeys.length
      }
    >
      <div className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-stone-800 dark:bg-stone-900">
        {detailFields.length > 0 && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {detailFields.map((field) => (
              <div key={field.label} className="min-w-0">
                <div className="text-[10px] font-medium uppercase text-stone-400 dark:text-stone-500">
                  {field.label}
                </div>
                <div
                  className="mt-0.5 truncate text-xs text-stone-700 dark:text-stone-200"
                  title={field.value ?? undefined}
                >
                  {field.value}
                </div>
              </div>
            ))}
          </div>
        )}
        {contextProvenance.referencedMaterials.length > 0 && (
          <div className="mt-2 min-w-0">
            <div className="text-[10px] font-medium uppercase text-stone-400 dark:text-stone-500">
              {t("runPlayback.context.referencedMaterials")}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {contextProvenance.referencedMaterials.map((item) => (
                <span
                  key={item.label}
                  className="inline-flex items-center gap-1 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] text-stone-600 dark:border-stone-800 dark:bg-stone-950/50 dark:text-stone-300"
                >
                  <span>{t(`runPlayback.context.counts.${item.label}`)}</span>
                  <span className="font-semibold tabular-nums">{item.value}</span>
                </span>
              ))}
            </div>
          </div>
        )}
        {contextProvenance.inputKeys.length > 0 && (
          <div className="mt-2 min-w-0">
            <div className="text-[10px] font-medium uppercase text-stone-400 dark:text-stone-500">
              {t("runPlayback.context.inputKeys")}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {contextProvenance.inputKeys.map((key) => (
                <span
                  key={key}
                  className="rounded-md bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 text-[10px] font-medium text-stone-600 dark:bg-stone-800 dark:text-stone-300"
                >
                  {key}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </PanelSection>
  );
}

function LoadingBlock() {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-2 text-sm text-stone-500 dark:border-stone-800 dark:bg-stone-950/50 dark:text-stone-400">
      <LoadingSpinner size="sm" />
      <span>{t("runPlayback.loading")}</span>
    </div>
  );
}

function ErrorBlock({
  message,
  onRetry,
}: {
  message: string | null;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-3 dark:border-red-900/50 dark:bg-red-950/30">
      <div className="flex items-start gap-2">
        <AlertTriangle
          size={16}
          className="mt-0.5 shrink-0 text-red-500 dark:text-red-400"
        />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-red-700 dark:text-red-300">
            {t("runPlayback.error")}
          </div>
          <div className="mt-1 text-xs text-red-600 dark:text-red-300/80">
            {message || t("runPlayback.errorFallback")}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex h-8 items-center gap-2 rounded-md border border-red-200 bg-[var(--theme-bg-card)] px-2.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 dark:border-red-900/60 dark:bg-red-950/20 dark:text-red-300 dark:hover:bg-red-950/40"
      >
        <RefreshCw size={13} />
        {t("runPlayback.retry")}
      </button>
    </div>
  );
}

function EmptyBlock() {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-3 py-4 text-center text-sm text-stone-500 dark:border-stone-800 dark:bg-stone-950/50 dark:text-stone-400">
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
    <div className="flex gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-stone-800 dark:bg-stone-900">
      <StatusIcon status={item.status} kind={item.kind} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-medium text-stone-800 dark:text-stone-100">
            {item.label}
          </span>
          <StatusBadge status={item.status} />
        </div>
        <div className="mt-1 flex min-w-0 flex-wrap gap-x-2 gap-y-1 text-[11px] text-stone-500 dark:text-stone-500">
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
    <div className="rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-stone-800 dark:bg-stone-900">
      <div className="flex min-w-0 items-start gap-2">
        <FileText
          size={15}
          className="mt-0.5 shrink-0 text-stone-400 dark:text-stone-500"
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-xs font-medium text-stone-800 dark:text-stone-100">
              {artifact.label}
            </span>
            <StatusBadge status={artifact.status} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-stone-500 dark:text-stone-500">
            {artifact.type && <span>{artifact.type}</span>}
            {artifact.contentType && <span>{artifact.contentType}</span>}
            {artifact.sizeLabel && <span>{artifact.sizeLabel}</span>}
          </div>
        </div>
      </div>
      {(artifact.downloadUrl || previewRequest) && (
        <div className="mt-2 flex flex-wrap gap-1.5 pl-6">
          {previewRequest && (
            <button
              type="button"
              onClick={handlePreview}
              aria-label={`${t("runPlayback.preview")} ${artifact.label}`}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-[11px] font-medium text-stone-600 transition-colors hover:bg-[var(--theme-bg-sidebar)] dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
            >
              <ExternalLink size={12} />
              {t("runPlayback.preview")}
            </button>
          )}
          {artifact.downloadUrl && (
            <button
              type="button"
              onClick={handleDownload}
              aria-label={`${t("runPlayback.download")} ${artifact.label}`}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-[var(--theme-border)] px-2 text-[11px] font-medium text-stone-600 transition-colors hover:bg-[var(--theme-bg-sidebar)] dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
            >
              <Download size={12} />
              {t("runPlayback.download")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function MultiAgentSection({
  counts,
  steps,
}: {
  counts: { label: string; value: number }[];
  steps: RunPlaybackStepItem[];
}) {
  const { t } = useTranslation();
  return (
    <PanelSection
      icon={<Users size={14} />}
      title={t("runPlayback.multiAgent")}
      count={steps.length}
    >
      {counts.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {counts.map((count) => (
            <span
              key={count.label}
              className="inline-flex items-center gap-1 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] text-stone-600 dark:border-stone-800 dark:bg-stone-950/50 dark:text-stone-300"
            >
              <span>{translateStatus(t, count.label)}</span>
              <span className="font-semibold tabular-nums">{count.value}</span>
            </span>
          ))}
        </div>
      )}
      {steps.length === 0 ? (
        <SectionEmpty label={t("runPlayback.noSteps")} />
      ) : (
        <div className="space-y-1.5">
          {steps.map((step) => (
            <StepRow key={step.id} step={step} />
          ))}
        </div>
      )}
    </PanelSection>
  );
}

function StepRow({ step }: { step: RunPlaybackStepItem }) {
  return (
    <div className="flex gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2.5 py-2 dark:border-stone-800 dark:bg-stone-900">
      <StatusIcon status={step.status} kind="step" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-medium text-stone-800 dark:text-stone-100">
            {step.label}
          </span>
          <StatusBadge status={step.status} />
        </div>
        <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-stone-500 dark:text-stone-500">
          {step.sequence !== null && <span>#{step.sequence}</span>}
          {step.role && <span>{step.role}</span>}
          {step.kind && <span>{step.kind}</span>}
          {step.startedAt && <span>{step.startedAt}</span>}
        </div>
      </div>
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
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-stone-700 dark:text-stone-200">
        <span className="text-stone-400 dark:text-stone-500">{icon}</span>
        <span>{title}</span>
        <span className="ml-auto rounded-md bg-[var(--theme-bg-sidebar)] px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-stone-500 dark:bg-stone-800 dark:text-stone-400">
          {count}
        </span>
      </div>
      {children}
    </section>
  );
}

function SectionEmpty({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-dashed border-stone-200 px-3 py-2 text-xs text-stone-500 dark:border-stone-800 dark:text-stone-500">
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
    return <LoadingSpinner size="xs" className="mt-0.5 shrink-0" />;
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
    return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300";
  }
  if (status === "error") {
    return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300";
  }
  if (status === "running" || status === "loading") {
    return "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";
  }
  if (status === "cancelled" || status === "blocked") {
    return "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300";
  }
  return "bg-[var(--theme-bg-sidebar)] text-stone-500 dark:bg-stone-900 dark:text-stone-400";
}

function getStatusIconClass(status: RunPlaybackDisplayStatus): string {
  if (status === "success") return "text-emerald-500 dark:text-emerald-400";
  if (status === "error") return "text-red-500 dark:text-red-400";
  if (status === "cancelled" || status === "blocked") {
    return "text-stone-500 dark:text-stone-400";
  }
  return "text-stone-400 dark:text-stone-500";
}

function translateStatus(t: TFunction, status: string): string {
  return t(`runPlayback.status.${status}`, { defaultValue: status });
}
