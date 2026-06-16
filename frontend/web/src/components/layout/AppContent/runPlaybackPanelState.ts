import type {
  RunPlaybackArtifact,
  RunPlaybackContextRef,
  RunPlaybackEvent,
  RunPlaybackResponse,
  RunPlaybackStep,
  RunPlaybackTimelineEntry,
} from "../../../services/api/runPlayback";

export type RunPlaybackPanelState = "loading" | "error" | "empty" | "ready";

export type RunPlaybackDisplayStatus =
  | "idle"
  | "loading"
  | "pending"
  | "running"
  | "success"
  | "error"
  | "cancelled"
  | "blocked"
  | "reused";

export interface RunPlaybackPanelSummary {
  runId: string | null;
  sessionId: string | null;
  agentId: string | null;
  traceId: string | null;
  status: string | null;
  progressLabel: string | null;
  errorMessage: string | null;
}

export interface RunPlaybackTimelineItem {
  id: string;
  kind: "event" | "artifact" | "step";
  label: string;
  status: RunPlaybackDisplayStatus;
  sequence: number | null;
  createdAt: string | null;
  detail: string | null;
}

export interface RunPlaybackArtifactItem {
  id: string;
  label: string;
  type: string | null;
  status: RunPlaybackDisplayStatus;
  contentType: string | null;
  sizeLabel: string | null;
  downloadUrl: string | null;
  previewUrl: string | null;
  createdAt: string | null;
}

export interface RunPlaybackStepItem {
  id: string;
  label: string;
  role: string | null;
  kind: string | null;
  status: RunPlaybackDisplayStatus;
  sequence: number | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface RunPlaybackCountItem {
  label: string;
  value: number;
}

export interface RunPlaybackMultiAgentViewModel {
  counts: RunPlaybackCountItem[];
  steps: RunPlaybackStepItem[];
}

export interface RunPlaybackContextProvenanceViewModel {
  source: string | null;
  executionTier: string | null;
  contextPackVersion: string | null;
  contextPackGeneratedAt: string | null;
  latestArtifactVersion: string | null;
  referencedMaterials: RunPlaybackCountItem[];
  inputKeys: string[];
}

export interface RunPlaybackPanelViewModel {
  state: RunPlaybackPanelState;
  summary: RunPlaybackPanelSummary;
  timeline: RunPlaybackTimelineItem[];
  artifacts: RunPlaybackArtifactItem[];
  multiAgent: RunPlaybackMultiAgentViewModel;
  contextProvenance: RunPlaybackContextProvenanceViewModel | null;
  errorMessage: string | null;
}

const EMPTY_MULTI_AGENT: RunPlaybackMultiAgentViewModel = {
  counts: [],
  steps: [],
};

const MULTI_AGENT_COUNT_ORDER = [
  "total",
  "succeeded",
  "running",
  "failed",
  "cancelled",
  "reused",
  "blocked",
  "pending",
] as const;

const PUBLIC_CONTEXT_INPUT_KEYS = new Set([
  "attachments",
  "message",
  "messages",
  "files",
  "file",
  "memory",
  "artifacts",
  "mode",
  "prompt",
  "query",
  "text",
]);

export function buildRunPlaybackLoadingViewModel(
  runId: string,
): RunPlaybackPanelViewModel {
  return {
    state: "loading",
    summary: buildFallbackSummary(runId, "loading", null),
    timeline: [],
    artifacts: [],
    multiAgent: EMPTY_MULTI_AGENT,
    contextProvenance: null,
    errorMessage: null,
  };
}

export function buildRunPlaybackErrorViewModel(
  runId: string,
  error: unknown,
): RunPlaybackPanelViewModel {
  const errorMessage = getErrorMessage(error);
  return {
    state: "error",
    summary: buildFallbackSummary(runId, "error", errorMessage),
    timeline: [],
    artifacts: [],
    multiAgent: EMPTY_MULTI_AGENT,
    contextProvenance: null,
    errorMessage,
  };
}

export function buildRunPlaybackPanelViewModel(
  response: RunPlaybackResponse | null | undefined,
): RunPlaybackPanelViewModel {
  const summary = buildSummary(response);
  const timeline = buildTimelineItems(response);
  const artifacts = buildArtifactItems(response);
  const multiAgent = buildMultiAgentViewModel(response);
  const contextProvenance = buildContextProvenanceViewModel(response?.context_ref);
  const hasContent =
    timeline.length > 0 ||
    artifacts.length > 0 ||
    multiAgent.counts.length > 0 ||
    multiAgent.steps.length > 0 ||
    contextProvenance !== null;

  return {
    state: hasContent ? "ready" : "empty",
    summary,
    timeline,
    artifacts,
    multiAgent,
    contextProvenance,
    errorMessage: null,
  };
}

function buildFallbackSummary(
  runId: string,
  status: string,
  errorMessage: string | null,
): RunPlaybackPanelSummary {
  return {
    runId,
    sessionId: null,
    agentId: null,
    traceId: null,
    status,
    progressLabel: null,
    errorMessage,
  };
}

function buildSummary(
  response: RunPlaybackResponse | null | undefined,
): RunPlaybackPanelSummary {
  const run = response?.run;
  return {
    runId: run?.run_id ?? response?.run_id ?? null,
    sessionId: run?.session_id ?? null,
    agentId: run?.agent_id ?? null,
    traceId: run?.trace_id ?? null,
    status: run?.status ?? null,
    progressLabel: formatProgress(run?.progress),
    errorMessage: run?.error_message ?? null,
  };
}

function buildTimelineItems(
  response: RunPlaybackResponse | null | undefined,
): RunPlaybackTimelineItem[] {
  const timeline = response?.timeline ?? [];
  if (timeline.length > 0) {
    return timeline.map((entry, index) => buildTimelineItem(entry, index));
  }

  return [
    ...(response?.events ?? []).map((event, index) =>
      buildEventTimelineItem(event, index),
    ),
    ...(response?.artifacts ?? []).map((artifact, index) =>
      buildArtifactTimelineItem(artifact, index),
    ),
    ...(response?.steps ?? []).map((step, index) =>
      buildStepTimelineItem(step, index),
    ),
  ];
}

function buildTimelineItem(
  entry: RunPlaybackTimelineEntry,
  index: number,
): RunPlaybackTimelineItem {
  if (entry.event) {
    return buildEventTimelineItem(
      entry.event,
      index,
      entry.sequence,
      entry.created_at,
    );
  }
  if (entry.artifact) {
    return buildArtifactTimelineItem(
      entry.artifact,
      index,
      entry.sequence,
      entry.created_at,
    );
  }
  if (entry.step) {
    return buildStepTimelineItem(
      entry.step,
      index,
      entry.sequence,
      entry.created_at,
    );
  }

  return {
    id: `timeline:${entry.sequence ?? index}`,
    kind: "event",
    label: entry.entry_type || "Event",
    status: "idle",
    sequence: toNullableNumber(entry.sequence),
    createdAt: entry.created_at ?? null,
    detail: null,
  };
}

function buildEventTimelineItem(
  event: RunPlaybackEvent,
  index: number,
  sequence: number | null | undefined = event.sequence,
  createdAt = event.created_at,
): RunPlaybackTimelineItem {
  const id = event.event_id ?? event.id ?? String(sequence ?? index);
  return {
    id: `event:${id}`,
    kind: "event",
    label: event.message || event.stage || event.event_type || event.type || "Event",
    status: getEventStatus(event),
    sequence: toNullableNumber(sequence),
    createdAt: createdAt ?? null,
    detail: event.event_type ?? event.type ?? null,
  };
}

function buildArtifactTimelineItem(
  artifact: RunPlaybackArtifact,
  index: number,
  sequence: number | null | undefined = null,
  createdAt = artifact.created_at,
): RunPlaybackTimelineItem {
  const id = artifact.artifact_id ?? artifact.id ?? String(sequence ?? index);
  return {
    id: `artifact:${id}`,
    kind: "artifact",
    label: getArtifactLabel(artifact),
    status: getArtifactStatus(artifact.status),
    sequence: toNullableNumber(sequence),
    createdAt: createdAt ?? null,
    detail: artifact.content_type ?? artifact.artifact_type ?? null,
  };
}

function buildStepTimelineItem(
  step: RunPlaybackStep,
  index: number,
  sequence: number | null | undefined = step.sequence,
  createdAt = step.created_at,
): RunPlaybackTimelineItem {
  const id = step.step_id ?? step.id ?? String(sequence ?? index);
  return {
    id: `step:${id}`,
    kind: "step",
    label: getStepLabel(step),
    status: getStepStatus(step.status),
    sequence: toNullableNumber(sequence),
    createdAt: createdAt ?? null,
    detail: step.role ?? step.step_kind ?? null,
  };
}

function buildArtifactItems(
  response: RunPlaybackResponse | null | undefined,
): RunPlaybackArtifactItem[] {
  const artifacts = new Map<string, RunPlaybackArtifact>();
  for (const artifact of response?.artifacts ?? []) {
    artifacts.set(getArtifactId(artifact, artifacts.size), artifact);
  }
  for (const entry of response?.timeline ?? []) {
    if (entry.artifact) {
      artifacts.set(getArtifactId(entry.artifact, artifacts.size), entry.artifact);
    }
  }
  return Array.from(artifacts.entries()).map(([id, artifact]) => ({
    id,
    label: getArtifactLabel(artifact),
    type: artifact.artifact_type ?? null,
    status: getArtifactStatus(artifact.status),
    contentType: artifact.content_type ?? null,
    sizeLabel: formatBytes(artifact.size_bytes),
    downloadUrl: artifact.download_url ?? null,
    previewUrl: artifact.preview_url ?? null,
    createdAt: artifact.created_at ?? null,
  }));
}

function buildMultiAgentViewModel(
  response: RunPlaybackResponse | null | undefined,
): RunPlaybackMultiAgentViewModel {
  const multiAgent = response?.multi_agent;
  if (!multiAgent) {
    return EMPTY_MULTI_AGENT;
  }

  return {
    counts: MULTI_AGENT_COUNT_ORDER.flatMap((label) => {
      const value = multiAgent.counts?.[label];
      return typeof value === "number" && value > 0 ? [{ label, value }] : [];
    }),
    steps: (multiAgent.steps ?? []).map((step, index) =>
      buildStepItem(step, index),
    ),
  };
}

function buildContextProvenanceViewModel(
  contextRef: RunPlaybackContextRef | null | undefined,
): RunPlaybackContextProvenanceViewModel | null {
  if (!contextRef) {
    return null;
  }
  const referencedMaterials = [
    countItem("files", contextRef.referenced_materials.file_count),
    countItem("messages", contextRef.referenced_materials.message_count),
    countItem("memory", contextRef.referenced_materials.memory_record_count),
    countItem("artifacts", contextRef.referenced_materials.artifact_count),
  ].filter((item): item is RunPlaybackCountItem => item !== null);
  const inputKeys = Array.from(
    new Set(
      (contextRef.used_context_summary.input_keys ?? []).filter((key) =>
        PUBLIC_CONTEXT_INPUT_KEYS.has(key),
      ),
    ),
  ).sort();
  const hasContent =
    Boolean(contextRef.source) ||
    Boolean(contextRef.execution_tier) ||
    Boolean(contextRef.context_pack_version) ||
    Boolean(contextRef.context_pack_generated_at) ||
    Boolean(contextRef.latest_artifact_version) ||
    referencedMaterials.length > 0 ||
    inputKeys.length > 0;
  if (!hasContent) {
    return null;
  }
  return {
    source: contextRef.source ?? contextRef.used_context_summary.source ?? null,
    executionTier: contextRef.execution_tier ?? null,
    contextPackVersion: contextRef.context_pack_version ?? null,
    contextPackGeneratedAt: contextRef.context_pack_generated_at ?? null,
    latestArtifactVersion: contextRef.latest_artifact_version ?? null,
    referencedMaterials,
    inputKeys,
  };
}

function countItem(
  label: string,
  value: number | undefined,
): RunPlaybackCountItem | null {
  return typeof value === "number" && value > 0 ? { label, value } : null;
}

function buildStepItem(
  step: RunPlaybackStep,
  index: number,
): RunPlaybackStepItem {
  return {
    id: step.step_id ?? step.id ?? String(step.sequence ?? index),
    label: getStepLabel(step),
    role: step.role ?? null,
    kind: step.step_kind ?? null,
    status: getStepStatus(step.status),
    sequence: toNullableNumber(step.sequence),
    startedAt: step.started_at ?? null,
    finishedAt: step.finished_at ?? null,
  };
}

function getEventStatus(event: RunPlaybackEvent): RunPlaybackDisplayStatus {
  const severity = normalizeStatus(event.severity);
  const type = normalizeStatus(event.event_type ?? event.type);
  if (event.error_code || severity === "error" || type.includes("fail")) {
    return "error";
  }
  if (type.includes("error")) return "error";
  if (type.includes("cancel")) return "cancelled";
  if (
    type.includes("complete") ||
    type.includes("success") ||
    type.includes("succeed") ||
    type.includes("done")
  ) {
    return "success";
  }
  if (
    type.includes("start") ||
    type.includes("running") ||
    type.includes("progress")
  ) {
    return "running";
  }
  return "idle";
}

function getArtifactStatus(
  status: string | undefined,
): RunPlaybackDisplayStatus {
  const normalized = normalizeStatus(status);
  if (!normalized) return "idle";
  if (normalized.includes("fail") || normalized.includes("error")) {
    return "error";
  }
  if (normalized.includes("cancel")) return "cancelled";
  if (normalized.includes("running") || normalized.includes("pending")) {
    return "running";
  }
  if (
    normalized.includes("ready") ||
    normalized.includes("created") ||
    normalized.includes("complete") ||
    normalized.includes("success") ||
    normalized.includes("succeed")
  ) {
    return "success";
  }
  return "idle";
}

function getStepStatus(status: string | undefined): RunPlaybackDisplayStatus {
  const normalized = normalizeStatus(status);
  if (!normalized) return "idle";
  if (normalized.includes("block")) return "blocked";
  if (normalized.includes("reuse")) return "reused";
  if (normalized.includes("pending") || normalized.includes("queued")) {
    return "pending";
  }
  if (normalized.includes("running") || normalized.includes("progress")) {
    return "running";
  }
  if (normalized.includes("fail") || normalized.includes("error")) {
    return "error";
  }
  if (normalized.includes("cancel")) return "cancelled";
  if (
    normalized.includes("complete") ||
    normalized.includes("success") ||
    normalized.includes("succeed")
  ) {
    return "success";
  }
  return "idle";
}

function getArtifactId(artifact: RunPlaybackArtifact, fallback: number): string {
  return artifact.artifact_id ?? artifact.id ?? String(fallback);
}

function getArtifactLabel(artifact: RunPlaybackArtifact): string {
  if (artifact.label) return artifact.label;
  if (artifact.artifact_type) return `${artifact.artifact_type} artifact`;
  return "Artifact";
}

function getStepLabel(step: RunPlaybackStep): string {
  if (step.title) return step.title;
  if (step.step_kind) return `${step.step_kind} step`;
  if (step.role) return `${step.role} step`;
  return "Step";
}

function formatProgress(progress: number | undefined): string | null {
  if (typeof progress !== "number" || !Number.isFinite(progress)) {
    return null;
  }
  return `${Math.round(progress)}%`;
}

function formatBytes(size: number | undefined): string | null {
  if (typeof size !== "number" || !Number.isFinite(size) || size < 0) {
    return null;
  }
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = size / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${units[unitIndex]}`;
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Failed to load run playback";
}

function normalizeStatus(status: string | undefined): string {
  return status?.trim().toLowerCase().replace(/_/g, ".") ?? "";
}

function toNullableNumber(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
