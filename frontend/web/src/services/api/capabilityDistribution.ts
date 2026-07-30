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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function invalidDistributionProjection(): never {
  throw new Error("capability_distribution_projection_invalid");
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
