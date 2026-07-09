import type {
  CapabilityDistributionListResponse,
  CapabilityDistributionUpdateRequest,
  CapabilityDistributionWriteResponse,
  CapabilityKind,
} from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

const CAPABILITY_DISTRIBUTION_ADMIN_API = `${API_BASE}/api/admin/capability-distributions`;

export function buildCapabilityDistributionUrl(
  capabilityKind: CapabilityKind,
  capabilityId: string,
): string {
  return `${CAPABILITY_DISTRIBUTION_ADMIN_API}/${encodeURIComponent(
    capabilityKind,
  )}/${encodeURIComponent(capabilityId)}`;
}

export function buildCapabilityDistributionListUrl(params: {
  capabilityKind: CapabilityKind;
  includeDisabled?: boolean;
}): string {
  const searchParams = new URLSearchParams();
  searchParams.set("capability_kind", params.capabilityKind);
  searchParams.set(
    "include_disabled",
    String(params.includeDisabled ?? true),
  );
  return `${CAPABILITY_DISTRIBUTION_ADMIN_API}?${searchParams.toString()}`;
}

export const capabilityDistributionApi = {
  async list(params: {
    capabilityKind: CapabilityKind;
    includeDisabled?: boolean;
  }): Promise<CapabilityDistributionListResponse> {
    return authFetch<CapabilityDistributionListResponse>(
      buildCapabilityDistributionListUrl(params),
    );
  },

  async update(
    capabilityKind: CapabilityKind,
    capabilityId: string,
    payload: CapabilityDistributionUpdateRequest,
  ): Promise<CapabilityDistributionWriteResponse> {
    return authFetch<CapabilityDistributionWriteResponse>(
      buildCapabilityDistributionUrl(capabilityKind, capabilityId),
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    );
  },

  async toggle(
    capabilityKind: CapabilityKind,
    capabilityId: string,
    enabled: boolean,
  ): Promise<CapabilityDistributionWriteResponse> {
    return authFetch<CapabilityDistributionWriteResponse>(
      `${buildCapabilityDistributionUrl(capabilityKind, capabilityId)}/toggle`,
      {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      },
    );
  },
};
