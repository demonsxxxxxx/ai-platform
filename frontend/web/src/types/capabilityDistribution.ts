export type CapabilityKind = "skill" | "mcp_server";

export type CapabilityDistributionStatus = "active" | "disabled";

export interface CapabilityDistribution {
  id: string;
  tenant_id: string;
  capability_kind: CapabilityKind;
  capability_id: string;
  status: CapabilityDistributionStatus;
  visible_to_user: boolean;
  scope_mode: "allowlist";
  department_ids: string[];
  allowed_roles: string[];
  metadata_json: Record<string, unknown>;
  updated_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CapabilityDistributionListResponse {
  items: CapabilityDistribution[];
  total: number;
}

export interface CapabilityDistributionUpdateRequest {
  status: CapabilityDistributionStatus;
  visible_to_user: boolean;
  scope_mode: "allowlist";
  department_ids: string[];
  allowed_roles: string[];
  metadata_json?: Record<string, unknown>;
}

export interface CapabilityDistributionWriteResponse {
  distribution: CapabilityDistribution;
  audit_id: string;
  audit_action: string;
}
