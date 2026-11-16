import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export interface RunPlaybackParams {
  after_sequence?: number | string | null;
  limit?: number | string | null;
}

export interface RunPlaybackFetchOptions extends RequestInit {
  skipAuth?: boolean;
}

export interface RunPlaybackRun {
  run_id?: string;
  session_id?: string;
  agent_id?: string;
  capability_id?: string | null;
  trace_id?: string;
  contract_version?: string;
  status?: string;
  progress?: number;
  cancel_requested_at?: string | null;
  cancel_requested_by?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export type RunPlaybackOutcomePhase =
  | "not_started"
  | "in_progress"
  | "completed"
  | "cancelled"
  | "delivery_failed"
  | "partially_completed"
  | "failed";

export interface RunPlaybackOutcome {
  schema_version: "ai-platform.public-run-outcome.v1";
  phase: RunPlaybackOutcomePhase;
  detail_code: string;
  what_happened: string;
  retained: string;
  next_action: string;
  problem_number: string;
  artifact_count?: number;
  completed_step_count?: number;
}

export interface RunPlaybackTokenCounts {
  input?: number;
  output?: number;
  total?: number;
}

export interface RunPlaybackCost {
  estimated_cost_minor?: number;
}

export interface RunPlaybackEvent {
  id?: string;
  event_id?: string;
  schema_version?: string;
  sequence?: number;
  run_id?: string;
  trace_id?: string;
  event_type?: string;
  type?: string;
  stage?: string;
  message?: string;
  severity?: string;
  visible_to_user?: boolean;
  error_code?: string | null;
  latency_ms?: number | null;
  token_counts?: RunPlaybackTokenCounts;
  cost?: RunPlaybackCost;
  created_at?: string | null;
}

export type RunPlaybackJsonValue =
  | string
  | number
  | boolean
  | null
  | RunPlaybackJsonValue[]
  | { [key: string]: RunPlaybackJsonValue };

export interface RunPlaybackArtifact {
  id?: string;
  artifact_id?: string;
  artifact_type?: string;
  label?: string;
  content_type?: string;
  size_bytes?: number;
  download_url?: string;
  preview_url?: string | null;
  status?: string;
  lineage?: { [key: string]: RunPlaybackJsonValue };
  created_at?: string | null;
}

export interface RunPlaybackStep {
  id?: string;
  step_id?: string;
  run_id?: string;
  step_key?: string;
  step_kind?: string;
  status?: string;
  title?: string;
  role?: string | null;
  sequence?: number;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RunPlaybackContextWindow {
  status: "complete" | "trimmed" | "degraded";
  history_candidate_count: number;
  history_authorized_count: number;
  history_omitted_count: number;
  legacy_history_excluded: boolean;
  selected_file_names: string[];
}

export interface RunPlaybackContextRef {
  context_window: RunPlaybackContextWindow;
}

export interface RunPlaybackTimelineEntry {
  entry_type?: string;
  sequence?: number | null;
  created_at?: string | null;
  event?: RunPlaybackEvent;
  artifact?: RunPlaybackArtifact;
  step?: RunPlaybackStep;
}

export interface RunPlaybackResponse {
  contract_version?: string;
  run_id?: string;
  after_sequence?: number | null;
  next_after_sequence?: number | null;
  run?: RunPlaybackRun;
  outcome?: RunPlaybackOutcome;
  timeline: RunPlaybackTimelineEntry[];
  events: RunPlaybackEvent[];
  artifacts: RunPlaybackArtifact[];
  steps: RunPlaybackStep[];
  context_ref?: RunPlaybackContextRef | null;
}

const PUBLIC_LINEAGE_KEYS = new Set([
  "source_event_id",
  "source_step_id",
  "source_file_id",
  "producer_kind",
  "producer_role",
  "checkpoint_id",
  "subagent_id",
]);

const HASH_LIKE_VALUE_PATTERN = /^(?:sha256:)?[a-f0-9]{40,}$/i;

const UNSAFE_LINEAGE_VALUE_FRAGMENTS = [
  "/",
  "\\",
  "=",
  ".claude",
  snakeKey("storage", "key"),
  snakeKey("runtime", "path"),
  snakeKey("work", "dir"),
  "manifest",
  snakeKey("command", "sha256"),
  snakeKey("sandbox", "mode"),
  snakeKey("mcp", "tool"),
  snakeKey("used", "skills"),
  snakeKey("resource", "limits"),
];

const UNSAFE_LINEAGE_VALUE_TOKENS = [
  "payload",
  "requestpayload",
  "decisionpayload",
  "manifest",
  "storagekey",
  "runtimepath",
  "workdir",
  "commandsha256",
  "sandboxmode",
  "mcptool",
  "usedskills",
  "resourcelimits",
  "rawskill",
];

function snakeKey(...parts: string[]): string {
  return parts.join("_");
}

export function buildRunPlaybackUrl(
  runId: string,
  params: RunPlaybackParams = {},
): string {
  const searchParams = new URLSearchParams();
  appendQueryParam(searchParams, "after_sequence", params.after_sequence);
  appendQueryParam(searchParams, "limit", params.limit);

  const query = searchParams.toString();
  return `${API_BASE}/api/ai/runs/${encodeURIComponent(runId)}/playback${
    query ? `?${query}` : ""
  }`;
}

export async function fetchRunPlayback(
  runId: string,
  params: RunPlaybackParams = {},
  options: RunPlaybackFetchOptions = {},
): Promise<RunPlaybackResponse> {
  const response = await authFetch<unknown>(
    buildRunPlaybackUrl(runId, params),
    {
      ...options,
      method: "GET",
    },
  );

  return normalizeRunPlayback(response);
}

// Normalize untrusted playback wire JSON. `unknown` permits validation of malformed
// data; it does not promise traversal or callback semantics for arbitrary objects
// with getters, proxies, or custom prototypes.
export function normalizeRunPlayback(
  response: unknown,
): RunPlaybackResponse {
  const source = asRecord(response) ?? {};

  return {
    contract_version: asString(source.contract_version),
    run_id: asString(source.run_id),
    after_sequence: asNumberOrNull(source.after_sequence),
    next_after_sequence: asNumberOrNull(source.next_after_sequence),
    run: normalizeRun(source.run),
    outcome: normalizeOutcome(source.outcome),
    timeline: normalizeTimeline(source.timeline),
    events: normalizeArray(source.events, normalizeEvent),
    artifacts: normalizeArray(source.artifacts, normalizeArtifact),
    steps: normalizeArray(source.steps, normalizeStep),
    context_ref: normalizeContextRef(source.context_ref),
  };
}

function normalizeOutcome(value: unknown): RunPlaybackOutcome | undefined {
  const source = asRecord(value);
  if (
    !source ||
    source.schema_version !== "ai-platform.public-run-outcome.v1"
  ) {
    return undefined;
  }
  const phase = asRunOutcomePhase(source.phase);
  const detailCode = asSafeCode(source.detail_code);
  const whatHappened = asBoundedText(source.what_happened);
  const retained = asBoundedText(source.retained);
  const nextAction = asBoundedText(source.next_action);
  const problemNumber = asProblemNumber(source.problem_number);
  if (
    !phase ||
    !detailCode ||
    !whatHappened ||
    !retained ||
    !nextAction ||
    !problemNumber
  ) {
    return undefined;
  }
  return compactObject({
    schema_version: "ai-platform.public-run-outcome.v1" as const,
    phase,
    detail_code: detailCode,
    what_happened: whatHappened,
    retained,
    next_action: nextAction,
    problem_number: problemNumber,
    artifact_count: asNonNegativeInteger(source.artifact_count),
    completed_step_count: asNonNegativeInteger(source.completed_step_count),
  });
}

function asRunOutcomePhase(value: unknown): RunPlaybackOutcomePhase | undefined {
  const candidate = asSafeCode(value);
  return candidate && RUN_OUTCOME_PHASES.has(candidate as RunPlaybackOutcomePhase)
    ? (candidate as RunPlaybackOutcomePhase)
    : undefined;
}

const RUN_OUTCOME_PHASES = new Set<RunPlaybackOutcomePhase>([
  "not_started",
  "in_progress",
  "completed",
  "cancelled",
  "delivery_failed",
  "partially_completed",
  "failed",
]);

function appendQueryParam(
  searchParams: URLSearchParams,
  key: string,
  value: number | string | null | undefined,
): void {
  if (value === undefined || value === null || value === "") {
    return;
  }
  searchParams.set(key, String(value));
}

function normalizeRun(value: unknown): RunPlaybackRun | undefined {
  const source = asRecord(value);
  if (!source) return undefined;

  return compactObject({
    run_id: asString(source.run_id),
    session_id: asString(source.session_id),
    agent_id: asString(source.agent_id),
    capability_id: asNullableString(source.capability_id),
    trace_id: asString(source.trace_id),
    contract_version: asString(source.contract_version),
    status: asString(source.status),
    progress: asNumber(source.progress),
    cancel_requested_at: asNullableString(source.cancel_requested_at),
    cancel_requested_by: asNullableString(source.cancel_requested_by),
    error_code: asNullableString(source.error_code),
    error_message: asNullableString(source.error_message),
  });
}

function normalizeEvent(value: unknown): RunPlaybackEvent {
  const source = asRecord(value);
  if (!source) return {};

  return compactObject({
    id: asString(source.id),
    event_id: asString(source.event_id),
    schema_version: asString(source.schema_version),
    sequence: asNumber(source.sequence),
    run_id: asString(source.run_id),
    trace_id: asString(source.trace_id),
    event_type: asString(source.event_type),
    type: asString(source.type),
    stage: asString(source.stage),
    message: asString(source.message),
    severity: asString(source.severity),
    visible_to_user: asBoolean(source.visible_to_user),
    error_code: asNullableString(source.error_code),
    latency_ms: asNumberOrNull(source.latency_ms),
    token_counts: normalizeTokenCounts(source.token_counts),
    cost: normalizeCost(source.cost),
    created_at: asNullableString(source.created_at),
  });
}

function normalizeTokenCounts(value: unknown): RunPlaybackTokenCounts | undefined {
  const source = asRecord(value);
  if (!source) return undefined;

  return compactObject({
    input: asNumber(source.input),
    output: asNumber(source.output),
    total: asNumber(source.total),
  });
}

function normalizeCost(value: unknown): RunPlaybackCost | undefined {
  const source = asRecord(value);
  if (!source) return undefined;

  return compactObject({
    estimated_cost_minor: typeof source.estimated_cost_minor === "number"
      ? asNumber(source.estimated_cost_minor) : undefined,
  });
}

function normalizeArtifact(value: unknown): RunPlaybackArtifact {
  const source = asRecord(value);
  if (!source) return {};

  const artifactId = asString(source.artifact_id) ?? asString(source.id);

  return compactObject({
    id: asString(source.id) ?? artifactId,
    artifact_id: artifactId,
    artifact_type: asString(source.artifact_type),
    label: asString(source.label),
    content_type: asString(source.content_type),
    size_bytes: asNumber(source.size_bytes),
    download_url: asString(source.download_url),
    preview_url: asNullableString(source.preview_url),
    status: asString(source.status),
    lineage: normalizeLineage(source.lineage),
    created_at: asNullableString(source.created_at),
  });
}

function normalizeStep(value: unknown): RunPlaybackStep {
  const source = asRecord(value);
  if (!source) return {};

  const stepId = asString(source.step_id) ?? asString(source.id);

  return compactObject({
    id: asString(source.id) ?? stepId,
    step_id: stepId,
    run_id: asString(source.run_id),
    step_key: asString(source.step_key),
    step_kind: asString(source.step_kind),
    status: asString(source.status),
    title: asString(source.title),
    role: asNullableString(source.role),
    sequence: asNumber(source.sequence),
    started_at: asNullableString(source.started_at),
    finished_at: asNullableString(source.finished_at),
    created_at: asNullableString(source.created_at),
    updated_at: asNullableString(source.updated_at),
  });
}

function normalizeContextRef(value: unknown): RunPlaybackContextRef {
  // The exact Run-bound projection owns this window. Do not merge the top-level
  // mirror or revive the retired provenance fields as a fallback authority.
  const source = asRecord(asRecord(value)?.context_window) ?? {};
  const validCounts = [
    source.history_candidate_count,
    source.history_authorized_count,
    source.history_omitted_count,
  ].every(isSafeContextCount);

  return {
    context_window: {
      status:
        validCounts && (source.status === "complete" || source.status === "trimmed")
          ? source.status
          : "degraded",
      history_candidate_count: safeContextCount(source.history_candidate_count),
      history_authorized_count: safeContextCount(source.history_authorized_count),
      history_omitted_count: safeContextCount(source.history_omitted_count),
      legacy_history_excluded: source.legacy_history_excluded === true,
      selected_file_names: safeContextFileNames(source.selected_file_names),
    },
  };
}

function isSafeContextCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function safeContextCount(value: unknown): number {
  return isSafeContextCount(value) ? value : 0;
}

function safeContextFileNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const names: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    // File names are public basenames, not lineage IDs: words such as manifest
    // and ordinary percent characters do not identify private metadata.
    const name = item.replace(/\\/g, "/").split("/").pop()?.trim();
    if (
      !name || name === "." || name === ".." || name.length > 160 ||
      /:/u.test(name) || /%(?:2f|5c)/iu.test(name) ||
      /(?:^|[._\s-])(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|secret|token)\s*=/iu.test(name) ||
      Array.from(name).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    ) {
      continue;
    }
    if (!names.includes(name)) names.push(name);
    if (names.length === 8) break;
  }
  return names;
}

function normalizeTimeline(value: unknown): RunPlaybackTimelineEntry[] {
  return normalizeArray(value, normalizeTimelineEntry)
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => compareTimelineEntries(left, right))
    .map(({ entry }) => entry);
}

function normalizeTimelineEntry(value: unknown): RunPlaybackTimelineEntry {
  const source = asRecord(value);
  if (!source) return {};

  const event = asRecord(source.event) ? normalizeEvent(source.event) : undefined;
  const artifact = asRecord(source.artifact)
    ? normalizeArtifact(source.artifact)
    : undefined;
  const step = asRecord(source.step) ? normalizeStep(source.step) : undefined;

  return compactObject({
    entry_type: asString(source.entry_type),
    sequence: asNumberOrNull(source.sequence),
    created_at: asNullableString(source.created_at),
    event: hasPublicFields(event) ? event : undefined,
    artifact: hasPublicFields(artifact) ? artifact : undefined,
    step: hasPublicFields(step) ? step : undefined,
  });
}

function compareTimelineEntries(
  left: { entry: RunPlaybackTimelineEntry; index: number },
  right: { entry: RunPlaybackTimelineEntry; index: number },
): number {
  const leftSequence =
    typeof left.entry.sequence === "number" ? left.entry.sequence : undefined;
  const rightSequence =
    typeof right.entry.sequence === "number" ? right.entry.sequence : undefined;

  if (leftSequence !== undefined && rightSequence !== undefined) {
    if (leftSequence !== rightSequence) {
      return leftSequence - rightSequence;
    }
  } else if (leftSequence !== undefined) {
    return -1;
  } else if (rightSequence !== undefined) {
    return 1;
  }

  const createdAtComparison = compareCreatedAt(
    left.entry.created_at,
    right.entry.created_at,
  );
  if (createdAtComparison !== 0) {
    return createdAtComparison;
  }

  return left.index - right.index;
}

function compareCreatedAt(
  left: string | null | undefined,
  right: string | null | undefined,
): number {
  const leftTime = asTime(left);
  const rightTime = asTime(right);

  if (leftTime !== undefined && rightTime !== undefined) {
    return leftTime - rightTime;
  }
  if (leftTime !== undefined) return -1;
  if (rightTime !== undefined) return 1;
  return 0;
}

function normalizeArray<T>(
  value: unknown,
  normalizeItem: (item: unknown) => T,
): T[] {
  return Array.isArray(value) ? value.map((item) => normalizeItem(item)) : [];
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asSafeCode(value: unknown): string | undefined {
  const candidate = asString(value)?.trim();
  return candidate && /^[a-z][a-z0-9_]{0,63}$/.test(candidate)
    ? candidate
    : undefined;
}

function asBoundedText(value: unknown): string | undefined {
  const candidate = asString(value)?.trim();
  return candidate && candidate.length <= 500 ? candidate : undefined;
}

function asProblemNumber(value: unknown): string | undefined {
  const candidate = asString(value)?.trim();
  return candidate && /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/.test(candidate)
    ? candidate
    : undefined;
}

function asNonNegativeInteger(value: unknown): number | undefined {
  const candidate = asNumber(value);
  return candidate !== undefined && Number.isInteger(candidate) && candidate >= 0
    ? candidate
    : undefined;
}

function asNullableString(value: unknown): string | null | undefined {
  if (value === null) return null;
  return asString(value);
}

function asNumber(value: unknown): number | undefined {
  const numberValue =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value)
        : undefined;
  return typeof numberValue === "number" && Number.isFinite(numberValue)
    ? numberValue
    : undefined;
}

function asNumberOrNull(value: unknown): number | null | undefined {
  if (value === null) return null;
  return asNumber(value);
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asTime(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : undefined;
}

function normalizeLineage(
  value: unknown,
): { [key: string]: RunPlaybackJsonValue } | undefined {
  const source = asRecord(value);
  if (!source) return undefined;

  const output: { [key: string]: RunPlaybackJsonValue } = {};
  for (const [key, childValue] of Object.entries(source)) {
    if (!PUBLIC_LINEAGE_KEYS.has(key)) {
      continue;
    }
    const sanitizedValue = sanitizeLineageValue(childValue);
    if (sanitizedValue !== undefined) {
      output[key] = sanitizedValue;
    }
  }
  return Object.keys(output).length ? output : undefined;
}

function sanitizeLineageValue(value: unknown): RunPlaybackJsonValue | undefined {
  // Lineage exposes scalars only; rejected JSON containers need no traversal.
  if (typeof value === "string") {
    return isSafeLineageString(value) ? value : undefined;
  }
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    return Number.isNaN(value) ? undefined : value;
  }
  return undefined;
}

function isSafeLineageString(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return false;
  if (HASH_LIKE_VALUE_PATTERN.test(normalized)) return false;
  if (
    UNSAFE_LINEAGE_VALUE_FRAGMENTS.some((fragment) =>
      normalized.includes(fragment),
    )
  ) {
    return false;
  }
  const compactNormalized = normalized.replace(/[^a-z0-9]/g, "");
  return !UNSAFE_LINEAGE_VALUE_TOKENS.some((token) =>
    compactNormalized.includes(token),
  );
}

function compactObject<T extends Record<string, unknown>>(value: T): T {
  for (const key of Object.keys(value)) {
    if (value[key] === undefined) {
      delete value[key];
    }
  }
  return value;
}

function hasPublicFields(value: unknown): boolean {
  const source = asRecord(value);
  return source ? Object.keys(source).length > 0 : false;
}
