import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  RoleGovernanceDecisionRequest,
  RoleGovernanceOperationResponse,
  RoleGovernanceOverviewResponse,
  RoleGovernanceRequestCreate,
  RoleGovernanceRequestItem,
  RoleGovernanceRollbackRequest,
} from "../../types/roleGovernance";

const ROLE_GOVERNANCE_OVERVIEW_API = `${API_BASE}/api/role-governance/overview`;
const ROLE_GOVERNANCE_REQUESTS_API = `${API_BASE}/api/role-governance/requests`;
const ROLE_GOVERNANCE_APPROVALS_API = `${API_BASE}/api/role-governance/approvals`;
const ROLE_GOVERNANCE_AUDIT_API = `${API_BASE}/api/role-governance/audit`;

function withWorkspace(url: string, workspaceId?: string) {
  const params = new URLSearchParams();
  if (workspaceId) params.set("workspace_id", workspaceId);
  const query = params.toString();
  return query ? `${url}?${query}` : url;
}

export const roleGovernanceApi = {
  getOverview(workspaceId = "default") {
    return authFetch<RoleGovernanceOverviewResponse>(
      withWorkspace(ROLE_GOVERNANCE_OVERVIEW_API, workspaceId),
    );
  },

  getRequest(requestId: string) {
    return authFetch<RoleGovernanceRequestItem>(
      `${ROLE_GOVERNANCE_REQUESTS_API}/${encodeURIComponent(requestId)}`,
    );
  },

  createRequest(payload: RoleGovernanceRequestCreate) {
    return authFetch<RoleGovernanceOperationResponse>(
      ROLE_GOVERNANCE_REQUESTS_API,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },

  approveRequest(
    requestId: string,
    payload: RoleGovernanceDecisionRequest = {},
  ) {
    return authFetch<RoleGovernanceOperationResponse>(
      `${ROLE_GOVERNANCE_APPROVALS_API}/${encodeURIComponent(
        requestId,
      )}/approve`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },

  rejectRequest(requestId: string, payload: RoleGovernanceDecisionRequest = {}) {
    return authFetch<RoleGovernanceOperationResponse>(
      `${ROLE_GOVERNANCE_APPROVALS_API}/${encodeURIComponent(
        requestId,
      )}/reject`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },

  rollbackAudit(auditId: string, payload: RoleGovernanceRollbackRequest = {}) {
    return authFetch<RoleGovernanceOperationResponse>(
      `${ROLE_GOVERNANCE_AUDIT_API}/${encodeURIComponent(auditId)}/rollback`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
  },
};
