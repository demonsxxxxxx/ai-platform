import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export type CapabilityDistributionStatus = "active" | "disabled";

export interface CapabilityDistribution {
  id: string;
  tenantId: string;
  capabilityKind: "skill" | "mcp_server";
  capabilityId: string;
  status: CapabilityDistributionStatus;
  visibleToUser: boolean;
  scopeMode: "allowlist";
  departmentIds: string[];
  allowedRoles: string[];
  metadata: Record<string, unknown>;
}

export interface CapabilityDistributionUpdate {
  status: CapabilityDistributionStatus;
  visibleToUser: boolean;
  scopeMode: "allowlist";
  departmentIds: string[];
  allowedRoles: string[];
  metadata: Record<string, unknown>;
}

export interface DepartmentDirectoryNode {
  directoryId: string;
  authorityId: string;
  name: string;
  path: string;
  children: DepartmentDirectoryNode[];
  selectable: boolean;
  reason: "duplicate_authority_id" | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function invalidDistributionProjection(): never {
  throw new Error("capability_distribution_projection_invalid");
}

const DEPARTMENT_DIRECTORY_ROOT_KEYS = ["departments"] as const;
const DEPARTMENT_DIRECTORY_NODE_KEYS = [
  "authority_id",
  "children",
  "directory_id",
  "name",
  "path",
  "reason",
  "selectable",
] as const;
const MAX_DEPARTMENT_DIRECTORY_NODES = 5_000;
const MAX_DEPARTMENT_DIRECTORY_DEPTH = 12;
const MAX_DEPARTMENT_DIRECTORY_LABEL_LENGTH = 160;

function hasExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === keys.length && actual.every((key, index) => key === keys[index]);
}

function isSafeDirectoryLabel(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    [...value].length <= MAX_DEPARTMENT_DIRECTORY_LABEL_LENGTH &&
    value === value.trim() &&
    !/\p{C}/u.test(value)
  );
}

function normalizeDepartmentDirectoryNode(
  value: unknown,
  seenDirectoryIds: Set<string>,
  state: { count: number },
  depth: number,
  parentPath: string,
): DepartmentDirectoryNode {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, DEPARTMENT_DIRECTORY_NODE_KEYS) ||
    depth > MAX_DEPARTMENT_DIRECTORY_DEPTH ||
    typeof value.directory_id !== "string" ||
    !/^[0-9]+$/.test(value.directory_id) ||
    seenDirectoryIds.has(value.directory_id) ||
    !isSafeDirectoryLabel(value.authority_id) ||
    value.name !== value.authority_id ||
    typeof value.path !== "string" ||
    value.path !== (parentPath ? `${parentPath} / ${value.name}` : value.name) ||
    !Array.isArray(value.children) ||
    typeof value.selectable !== "boolean" ||
    (value.reason !== null && value.reason !== "duplicate_authority_id") ||
    (value.selectable && value.reason !== null) ||
    (!value.selectable && value.reason !== "duplicate_authority_id")
  ) {
    invalidDistributionProjection();
  }
  state.count += 1;
  if (state.count > MAX_DEPARTMENT_DIRECTORY_NODES) {
    invalidDistributionProjection();
  }
  seenDirectoryIds.add(value.directory_id);
  const path = value.path;
  return {
    directoryId: value.directory_id,
    authorityId: value.authority_id,
    name: value.name,
    path,
    children: value.children.map((child) =>
      normalizeDepartmentDirectoryNode(child, seenDirectoryIds, state, depth + 1, path),
    ),
    selectable: value.selectable,
    reason: value.reason,
  };
}

export function normalizeDepartmentDirectory(value: unknown): DepartmentDirectoryNode[] {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, DEPARTMENT_DIRECTORY_ROOT_KEYS) ||
    !Array.isArray(value.departments)
  ) {
    invalidDistributionProjection();
  }
  const seenDirectoryIds = new Set<string>();
  const state = { count: 0 };
  return value.departments.map((node) =>
    normalizeDepartmentDirectoryNode(node, seenDirectoryIds, state, 1, ""),
  );
}

/** Build the administrator-only distribution listing URL for one capability kind. */
export function buildCapabilityDistributionListUrl(kind: "skill" | "mcp_server"): string {
  return `${API_BASE}/api/admin/capability-distributions?capability_kind=${encodeURIComponent(kind)}`;
}

/** Build the administrator-only update URL for one opaque capability identifier. */
export function buildCapabilityDistributionUrl(
  kind: "skill" | "mcp_server",
  capabilityId: string,
): string {
  return `${API_BASE}/api/admin/capability-distributions/${kind}/${encodeURIComponent(capabilityId)}`;
}

export function buildDepartmentDirectoryUrl(): string {
  return `${API_BASE}/api/admin/capability-distributions/department-directory`;
}

/** Accept only the typed server projection used by the governance editor. */
export function normalizeCapabilityDistribution(value: unknown): CapabilityDistribution {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.tenant_id !== "string" ||
    (value.capability_kind !== "skill" && value.capability_kind !== "mcp_server") ||
    typeof value.capability_id !== "string" ||
    (value.status !== "active" && value.status !== "disabled") ||
    typeof value.visible_to_user !== "boolean" ||
    value.scope_mode !== "allowlist" ||
    !isStringArray(value.department_ids) ||
    !isStringArray(value.allowed_roles) ||
    !isRecord(value.metadata_json)
  ) {
    invalidDistributionProjection();
  }

  return {
    id: value.id,
    tenantId: value.tenant_id,
    capabilityKind: value.capability_kind,
    capabilityId: value.capability_id,
    status: value.status,
    visibleToUser: value.visible_to_user,
    scopeMode: "allowlist",
    departmentIds: [...value.department_ids],
    allowedRoles: [...value.allowed_roles],
    metadata: { ...value.metadata_json },
  };
}

function normalizeCapabilityDistributionWriteResponse(value: unknown): CapabilityDistribution {
  if (!isRecord(value)) invalidDistributionProjection();
  return normalizeCapabilityDistribution(value.capability_distribution);
}

function serializeUpdate(update: CapabilityDistributionUpdate) {
  return {
    status: update.status,
    visible_to_user: update.visibleToUser,
    scope_mode: update.scopeMode,
    department_ids: update.departmentIds,
    allowed_roles: update.allowedRoles,
    metadata: update.metadata,
  };
}

export const capabilityDistributionApi = {
  async departmentDirectory(): Promise<DepartmentDirectoryNode[]> {
    const response = await authFetch<unknown>(buildDepartmentDirectoryUrl());
    return normalizeDepartmentDirectory(response);
  },

  async list(kind: "skill" | "mcp_server"): Promise<CapabilityDistribution[]> {
    const response = await authFetch<unknown>(
      buildCapabilityDistributionListUrl(kind),
    );
    if (!isRecord(response) || !Array.isArray(response.capability_distributions)) {
      invalidDistributionProjection();
    }
    return response.capability_distributions.map(normalizeCapabilityDistribution);
  },

  async update(
    kind: "skill" | "mcp_server",
    capabilityId: string,
    update: CapabilityDistributionUpdate,
  ): Promise<CapabilityDistribution> {
    const response = await authFetch<unknown>(
      buildCapabilityDistributionUrl(kind, capabilityId),
      {
        method: "PUT",
        body: JSON.stringify(serializeUpdate(update)),
      },
    );
    return normalizeCapabilityDistributionWriteResponse(response);
  },
};
