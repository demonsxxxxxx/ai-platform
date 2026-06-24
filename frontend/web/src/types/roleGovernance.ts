export interface RoleGovernanceWorkbenchGovernance {
  projection: string;
  tenant_id: string;
  workspace_id: string;
  degraded: boolean;
  audit_required: boolean;
  rollback_available: boolean;
  secret_material_projected: boolean;
}

export interface RoleGovernanceRole {
  role_id: string;
  name: string;
  description: string;
  requestable: boolean;
  assignable: boolean;
  scope: "tenant" | "department" | "workspace";
  capabilities: string[];
}

export interface RoleGovernanceRoleDirectory {
  roles: RoleGovernanceRole[];
}

export interface RoleGovernanceDepartment {
  department_id: string;
  name: string;
  current_user_member: boolean;
  requestable: boolean;
}

export interface RoleGovernanceWorkspace {
  workspace_id: string;
  name: string;
  current: boolean;
  requestable: boolean;
}

export interface RoleGovernanceSkillAvailability {
  skill_id: string;
  availability_state: "enabled" | "disabled" | "inherited" | "requestable";
  inherited_from: "tenant" | "department" | "workspace";
  scope_id: string;
}

export interface RoleGovernanceScope {
  tenant_id: string;
  workspace_id: string;
  current_department_id: string;
  departments: RoleGovernanceDepartment[];
  workspaces: RoleGovernanceWorkspace[];
  skill_availability: RoleGovernanceSkillAvailability[];
}

export interface RoleGovernanceRequestItem {
  request_id: string;
  requester_id: string;
  target_type: "role" | "department_agent";
  target_id: string;
  status: "pending" | "approved" | "rejected" | "queued";
  reason: string;
  approver_id?: string | null;
  created_at?: string | null;
  decided_at?: string | null;
  audit_id?: string | null;
}

export interface RoleGovernanceAuditItem {
  audit_id: string;
  action: string;
  target_type: string;
  target_id: string;
  actor_id: string;
  source: string;
  status: string;
  rollback_available: boolean;
  created_at?: string | null;
}

export interface RoleGovernanceOverviewResponse {
  governance: RoleGovernanceWorkbenchGovernance;
  role_directory: RoleGovernanceRoleDirectory;
  scope: RoleGovernanceScope;
  requests: RoleGovernanceRequestItem[];
  audit: RoleGovernanceAuditItem[];
}

export interface RoleGovernanceRequestCreate {
  target_type: "role" | "department_agent";
  target_id: string;
  reason?: string;
  workspace_id?: string;
}

export interface RoleGovernanceDecisionRequest {
  decision_note?: string;
  rollback_id?: string | null;
}

export interface RoleGovernanceRollbackRequest {
  reason?: string;
}

export interface RoleGovernanceOperationResponse {
  target_type: string;
  target_id: string;
  operation: string;
  status: string;
  audit_id: string;
  message: string;
}
