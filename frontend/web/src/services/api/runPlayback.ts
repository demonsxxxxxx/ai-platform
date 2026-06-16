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

export interface RunPlaybackMultiAgentCounts {
  total?: number;
  pending?: number;
  succeeded?: number;
  failed?: number;
  running?: number;
  cancelled?: number;
  reused?: number;
  blocked?: number;
}

export interface RunPlaybackMultiAgent {
  run_id?: string;
  steps: RunPlaybackStep[];
  counts: RunPlaybackMultiAgentCounts;
}

export interface RunPlaybackReferencedMaterials {
  file_count?: number;
  message_count?: number;
  memory_record_count?: number;
  artifact_count?: number;
}

export interface RunPlaybackUsedContextSummary {
  source?: string;
  input_keys: string[];
  file_count?: number;
  message_count?: number;
  memory_record_count?: number;
  artifact_count?: number;
  memory_policy_source?: string;
  long_term_memory_read?: boolean;
}

export interface RunPlaybackContextRef {
  source?: string;
  referenced_materials: RunPlaybackReferencedMaterials;
  used_context_summary: RunPlaybackUsedContextSummary;
  execution_tier?: string;
  latest_artifact_version?: string | null;
  context_pack_version?: string;
  context_pack_generated_at?: string;
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
  timeline: RunPlaybackTimelineEntry[];
  events: RunPlaybackEvent[];
  artifacts: RunPlaybackArtifact[];
  steps: RunPlaybackStep[];
  multi_agent: RunPlaybackMultiAgent | null;
  context_ref?: RunPlaybackContextRef | null;
}

const DANGEROUS_KEYS = new Set([
  "payload",
  "manifest",
  snakeKey("storage", "key"),
  snakeKey("runtime", "path"),
  snakeKey("work", "dir"),
  snakeKey("command", "sha256"),
  snakeKey("sandbox", "mode"),
  snakeKey("mcp", "tool", "ids"),
  snakeKey("used", "skills", "source"),
  snakeKey("resource", "limits"),
]);

const PUBLIC_LINEAGE_KEYS = new Set([
  "source_event_id",
  "source_step_id",
  "source_file_id",
  "producer_kind",
  "producer_role",
  "checkpoint_id",
  "subagent_id",
]);

const PUBLIC_CONTEXT_COUNTS = [
  "file_count",
  "message_count",
  "memory_record_count",
  "artifact_count",
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

const PUBLIC_CONTEXT_SOURCE_VALUES = new Set([
  "chat_stream",
  "copy_run",
  "manual_context_snapshot",
  "multi_agent_dispatch_handoff",
  "multi_agent_dispatch_tick",
  "resume_run",
  "retry_run",
  "runs_api",
  "stored_context_snapshot",
  "worker_refresh",
]);

const PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES = new Set([
  "default",
  "not_recorded",
  "stored",
]);

const PUBLIC_CONTEXT_EXECUTION_TIERS = new Set([
  "sdk_only_writing",
  "document_worker",
  "heavy_sandbox",
]);

const HASH_LIKE_VALUE_PATTERN = /^(?:sha256:)?[a-f0-9]{40,}$/i;
const PUBLIC_VERSION_PATTERN = /^v\d+(?:[._:-]\d+){0,3}$/i;

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
  const response = await authFetch<RunPlaybackResponse | null>(
    buildRunPlaybackUrl(runId, params),
    {
      ...options,
      method: "GET",
    },
  );

  return normalizeRunPlayback(response);
}

export function normalizeRunPlayback(
  response: RunPlaybackResponse | null | undefined,
): RunPlaybackResponse {
  const source = asRecord(response) ?? {};

  return {
    contract_version: asString(source.contract_version),
    run_id: asString(source.run_id),
    after_sequence: asNumberOrNull(source.after_sequence),
    next_after_sequence: asNumberOrNull(source.next_after_sequence),
    run: normalizeRun(source.run),
    timeline: normalizeTimeline(source.timeline),
    events: normalizeArray(source.events, normalizeEvent),
    artifacts: normalizeArray(source.artifacts, normalizeArtifact),
    steps: normalizeArray(source.steps, normalizeStep),
    multi_agent: normalizeMultiAgent(source.multi_agent),
    context_ref: normalizeContextRef(source.context_ref),
  };
}

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
    estimated_cost_minor: asNumber(source.estimated_cost_minor),
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

function normalizeMultiAgent(value: unknown): RunPlaybackMultiAgent | null {
  const source = asRecord(value);
  if (!source) return null;

  return {
    run_id: asString(source.run_id),
    steps: normalizeArray(source.steps, normalizeStep),
    counts: normalizeMultiAgentCounts(source.counts),
  };
}

function normalizeMultiAgentCounts(value: unknown): RunPlaybackMultiAgentCounts {
  const source = asRecord(value);
  if (!source) return {};

  return compactObject({
    total: asNumber(source.total),
    pending: asNumber(source.pending),
    succeeded: asNumber(source.succeeded),
    failed: asNumber(source.failed),
    running: asNumber(source.running),
    cancelled: asNumber(source.cancelled),
    reused: asNumber(source.reused),
    blocked: asNumber(source.blocked),
  });
}

function normalizeContextRef(value: unknown): RunPlaybackContextRef | null {
  const source = asRecord(value);
  if (!source) return null;

  const usedSummary = normalizeUsedContextSummary(source.used_context_summary);
  const referencedMaterials = normalizeReferencedMaterials(
    source.referenced_materials,
  );
  return compactObject({
    source: safeContextSource(source.source),
    referenced_materials: referencedMaterials,
    used_context_summary: usedSummary,
    execution_tier: safeContextExecutionTier(source.execution_tier),
    latest_artifact_version: safeContextVersion(
      source.latest_artifact_version,
    ),
    context_pack_version: safeContextVersion(source.context_pack_version) ?? undefined,
    context_pack_generated_at: safeIsoDateTime(source.context_pack_generated_at),
  });
}

function normalizeReferencedMaterials(
  value: unknown,
): RunPlaybackReferencedMaterials {
  const source = asRecord(value);
  const output: RunPlaybackReferencedMaterials = {};
  if (!source) return output;
  for (const key of PUBLIC_CONTEXT_COUNTS) {
    const count = asNumber(source[key]);
    if (count !== undefined && count >= 0) {
      output[key] = count;
    }
  }
  return output;
}

function normalizeUsedContextSummary(
  value: unknown,
): RunPlaybackUsedContextSummary {
  const source = asRecord(value);
  if (!source) return { input_keys: [] };
  const output: RunPlaybackUsedContextSummary = {
    input_keys: normalizeContextInputKeys(source.input_keys),
  };
  const summarySource = safeContextSource(source.source);
  if (summarySource) output.source = summarySource;
  for (const key of PUBLIC_CONTEXT_COUNTS) {
    const count = asNumber(source[key]);
    if (count !== undefined && count >= 0) {
      output[key] = count;
    }
  }
  const memoryPolicySource = asString(source.memory_policy_source);
  if (
    memoryPolicySource &&
    PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES.has(memoryPolicySource)
  ) {
    output.memory_policy_source = memoryPolicySource;
  }
  const longTermMemoryRead = asBoolean(source.long_term_memory_read);
  if (longTermMemoryRead !== undefined) {
    output.long_term_memory_read = longTermMemoryRead;
  }
  return output;
}

function safeContextSource(value: unknown): string | undefined {
  const source = asString(value);
  return source && PUBLIC_CONTEXT_SOURCE_VALUES.has(source) ? source : undefined;
}

function safeContextExecutionTier(value: unknown): string | undefined {
  const tier = asString(value);
  return tier && PUBLIC_CONTEXT_EXECUTION_TIERS.has(tier) ? tier : undefined;
}

function safeContextVersion(value: unknown): string | null | undefined {
  if (value === null) return null;
  const version = asString(value)?.trim();
  if (!version) return undefined;
  if (!PUBLIC_VERSION_PATTERN.test(version)) return undefined;
  if (HASH_LIKE_VALUE_PATTERN.test(version)) return undefined;
  if (!isSafeLineageString(version)) return undefined;
  return version;
}

function safeIsoDateTime(value: unknown): string | undefined {
  const timestamp = asString(value)?.trim();
  if (!timestamp) return undefined;
  if (asTime(timestamp) === undefined) return undefined;
  const normalized = timestamp.toLowerCase();
  if (
    normalized.includes("/") ||
    normalized.includes("\\") ||
    normalized.includes(snakeKey("storage", "key")) ||
    normalized.includes(snakeKey("runtime", "path")) ||
    normalized.includes(snakeKey("work", "dir")) ||
    normalized.includes("payload")
  ) {
    return undefined;
  }
  return timestamp;
}

function normalizeContextInputKeys(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return Array.from(
    new Set(
      value.flatMap((item) => {
        const key = asString(item)?.trim();
        if (!key || !PUBLIC_CONTEXT_INPUT_KEYS.has(key)) {
          return [];
        }
        return [key];
      }),
    ),
  ).sort();
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

function sanitizePublicJsonValue(
  value: unknown,
): RunPlaybackJsonValue | undefined {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return Number.isNaN(value) ? undefined : value;
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => sanitizePublicJsonValue(item))
      .filter((item): item is RunPlaybackJsonValue => item !== undefined);
  }

  const source = asRecord(value);
  if (!source) return undefined;

  const output: { [key: string]: RunPlaybackJsonValue } = {};
  for (const [key, childValue] of Object.entries(source)) {
    if (DANGEROUS_KEYS.has(key)) {
      continue;
    }
    const sanitizedValue = sanitizePublicJsonValue(childValue);
    if (sanitizedValue !== undefined) {
      output[key] = sanitizedValue;
    }
  }
  return output;
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
  const sanitizedValue = sanitizePublicJsonValue(value);
  if (
    sanitizedValue === undefined ||
    Array.isArray(sanitizedValue) ||
    (typeof sanitizedValue === "object" && sanitizedValue !== null)
  ) {
    return undefined;
  }
  if (typeof sanitizedValue === "string" && !isSafeLineageString(sanitizedValue)) {
    return undefined;
  }
  return sanitizedValue;
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
