/**
 * Governed channel projections returned by ai-platform public/admin APIs.
 */

export interface PublicChannelResponse {
  channel_id: string;
  workspace_id: string;
  display_name: string;
  channel_type: string;
  enabled: boolean;
  capabilities: string[];
  connection_state: string;
  redaction_policy: string;
  retention_policy: string;
  last_actor?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PublicChannelsResponse {
  tenant_id: string;
  workspace_id: string;
  channels: PublicChannelResponse[];
  total: number;
}

export interface ChannelAdminOperationResponse {
  channel_id: string;
  workspace_id: string;
  operation: string;
  status: string;
  audit_id: string;
  message: string;
}
