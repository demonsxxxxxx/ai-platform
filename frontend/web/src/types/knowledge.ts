export type KnowledgeConnectionStatus =
  | "draft"
  | "checking"
  | "cataloging"
  | "active"
  | "unavailable"
  | "disabled";

export interface KnowledgeConnection {
  id: string;
  name: string;
  provider_key: "ragflow";
  base_url: string;
  status: KnowledgeConnectionStatus;
  lifecycle_epoch: number;
  credential_state: "configured";
  credential_fingerprint: string;
  candidate_revision_id: string | null;
  active_revision_id: string | null;
  active_catalog_sync_id: string | null;
  last_authenticated_check_at: string | null;
  last_complete_sync_at: string | null;
  safe_failure_code: string | null;
  source_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface KnowledgeSource {
  id: string;
  connection_id: string;
  connection_name: string;
  name: string;
  provider_name: string;
  description: string;
  status: "pending_review" | "active" | "disabled" | "missing";
  authorization_version: number;
  visibility: "enterprise" | "restricted";
  allowed_department_ids: string[];
  allowed_roles: string[];
  allowed_user_ids: string[];
  first_seen_at: string | null;
  last_seen_at: string | null;
  last_complete_sync_at: string | null;
  connection_status: KnowledgeConnectionStatus;
}

export interface KnowledgeCursorPage<T> {
  items: T[];
  next_cursor: string | null;
  limit: number;
}

export interface KnowledgeCatalogSync {
  id: string;
  connection_id: string;
  connection_revision_id: string;
  status: string;
  purpose: string;
  observed_count: number;
  page_count: number;
  safe_failure_code: string | null;
  requested_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface KnowledgeBuilderSource {
  id: string;
  name: string;
  description: string;
  authorization_version: number;
  connection_name: string;
  last_seen_at: string | null;
  available: boolean;
  source_status: KnowledgeSource["status"];
  connection_status: KnowledgeConnectionStatus;
  visibility: KnowledgeSource["visibility"];
  allowed_department_count: number;
  allowed_department_ids: string[];
  allowed_roles: string[];
  allowed_user_ids: string[];
}

export interface KnowledgeRetrievalProfile {
  id: string;
  revision: number;
  name: string;
  description: string;
  status: "active";
  content_hash: string;
}

export interface KnowledgeBuilderCatalog {
  sources: KnowledgeBuilderSource[];
  next_cursor: string | null;
  limit: number;
  retrieval_profiles: KnowledgeRetrievalProfile[];
}
