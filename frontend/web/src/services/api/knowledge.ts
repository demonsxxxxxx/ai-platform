import type {
  KnowledgeCatalogSync,
  KnowledgeBuilderCatalog,
  KnowledgeConnection,
  KnowledgeCursorPage,
  KnowledgeSource,
} from "../../types/knowledge";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

const INVALID_KNOWLEDGE_RESPONSE = "invalid_knowledge_response";

function record(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  }
  return value as Record<string, unknown>;
}

function stringValue(value: unknown, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
    throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  }
  return value;
}

function nullableString(value: unknown): string | null {
  if (value === null) return null;
  return stringValue(value);
}

function integerValue(value: unknown, minimum = 0): number {
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  }
  return value as number;
}

function booleanValue(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  return value;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  }
  return [...value];
}

function oneOf<const T extends readonly string[]>(value: unknown, allowed: T): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  }
  return value as T[number];
}

const CONNECTION_STATUSES = [
  "draft",
  "checking",
  "cataloging",
  "active",
  "unavailable",
  "disabled",
] as const;
const SOURCE_STATUSES = ["pending_review", "active", "disabled", "missing"] as const;
const VISIBILITIES = ["enterprise", "restricted"] as const;

export function projectKnowledgeConnection(value: unknown): KnowledgeConnection {
  const item = record(value);
  return {
    id: stringValue(item.id),
    name: stringValue(item.name),
    provider_key: oneOf(item.provider_key, ["ragflow"] as const),
    base_url: stringValue(item.base_url),
    status: oneOf(item.status, CONNECTION_STATUSES),
    lifecycle_epoch: integerValue(item.lifecycle_epoch),
    credential_state: oneOf(item.credential_state, ["configured"] as const),
    credential_fingerprint: stringValue(item.credential_fingerprint, true),
    candidate_revision_id: nullableString(item.candidate_revision_id),
    active_revision_id: nullableString(item.active_revision_id),
    active_catalog_sync_id: nullableString(item.active_catalog_sync_id),
    last_authenticated_check_at: nullableString(item.last_authenticated_check_at),
    last_complete_sync_at: nullableString(item.last_complete_sync_at),
    safe_failure_code: nullableString(item.safe_failure_code),
    source_count: integerValue(item.source_count),
    created_at: nullableString(item.created_at),
    updated_at: nullableString(item.updated_at),
  };
}

export function projectKnowledgeSource(value: unknown): KnowledgeSource {
  const item = record(value);
  return {
    id: stringValue(item.id),
    connection_id: stringValue(item.connection_id),
    connection_name: stringValue(item.connection_name),
    name: stringValue(item.name),
    provider_name: stringValue(item.provider_name),
    description: stringValue(item.description, true),
    status: oneOf(item.status, SOURCE_STATUSES),
    authorization_version: integerValue(item.authorization_version, 1),
    visibility: oneOf(item.visibility, VISIBILITIES),
    allowed_department_ids: stringList(item.allowed_department_ids),
    allowed_roles: stringList(item.allowed_roles),
    allowed_user_ids: stringList(item.allowed_user_ids),
    first_seen_at: nullableString(item.first_seen_at),
    last_seen_at: nullableString(item.last_seen_at),
    last_complete_sync_at: nullableString(item.last_complete_sync_at),
    connection_status: oneOf(item.connection_status, CONNECTION_STATUSES),
  };
}

function projectKnowledgeSync(value: unknown): KnowledgeCatalogSync {
  const item = record(value);
  return {
    id: stringValue(item.id),
    connection_id: stringValue(item.connection_id),
    connection_revision_id: stringValue(item.connection_revision_id),
    status: stringValue(item.status),
    purpose: stringValue(item.purpose),
    observed_count: integerValue(item.observed_count),
    page_count: integerValue(item.page_count),
    safe_failure_code: nullableString(item.safe_failure_code),
    requested_at: nullableString(item.requested_at),
    started_at: nullableString(item.started_at),
    completed_at: nullableString(item.completed_at),
  };
}

function projectCursorPage<T>(
  value: unknown,
  projectItem: (item: unknown) => T,
): KnowledgeCursorPage<T> {
  const page = record(value);
  if (!Array.isArray(page.items)) throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  return {
    items: page.items.map(projectItem),
    next_cursor: nullableString(page.next_cursor),
    limit: integerValue(page.limit, 1),
  };
}

export function projectKnowledgeBuilderCatalog(value: unknown): KnowledgeBuilderCatalog {
  const catalog = record(value);
  if (!Array.isArray(catalog.sources) || !Array.isArray(catalog.retrieval_profiles)) {
    throw new Error(INVALID_KNOWLEDGE_RESPONSE);
  }
  return {
    sources: catalog.sources.map((source) => {
      const item = record(source);
      return {
        id: stringValue(item.id),
        name: stringValue(item.name),
        description: stringValue(item.description, true),
        authorization_version: integerValue(item.authorization_version, 1),
        connection_name: stringValue(item.connection_name),
        last_seen_at: nullableString(item.last_seen_at),
        available: booleanValue(item.available),
        source_status: oneOf(item.source_status, SOURCE_STATUSES),
        connection_status: oneOf(item.connection_status, CONNECTION_STATUSES),
        visibility: oneOf(item.visibility, VISIBILITIES),
        allowed_department_count: integerValue(item.allowed_department_count),
        allowed_department_ids: stringList(item.allowed_department_ids),
        allowed_roles: stringList(item.allowed_roles),
        allowed_user_ids: stringList(item.allowed_user_ids),
      };
    }),
    next_cursor: nullableString(catalog.next_cursor),
    limit: integerValue(catalog.limit, 1),
    retrieval_profiles: catalog.retrieval_profiles.map((profile) => {
      const item = record(profile);
      return {
        id: stringValue(item.id),
        revision: integerValue(item.revision, 1),
        name: stringValue(item.name),
        description: stringValue(item.description, true),
        status: oneOf(item.status, ["active"] as const),
        content_hash: stringValue(item.content_hash),
      };
    }),
  };
}

function operationId(): string {
  return crypto.randomUUID();
}

function queryString(
  values: Record<string, string | number | null | undefined>,
): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

const base = `${API_BASE}/api/ai/admin/knowledge`;

export const knowledgeApi = {
  builderCatalog(params: {
    limit?: number;
    cursor?: string | null;
    q?: string;
    selectedSourceIds?: readonly string[];
  } = {}): Promise<KnowledgeBuilderCatalog> {
    const search = new URLSearchParams();
    search.set("limit", String(params.limit ?? 50));
    if (params.cursor) search.set("cursor", params.cursor);
    if (params.q?.trim()) search.set("q", params.q.trim());
    params.selectedSourceIds?.forEach((sourceId) => {
      search.append("selected_source_id", sourceId);
    });
    return authFetch<unknown>(`${base}/builder-catalog?${search.toString()}`).then(
      projectKnowledgeBuilderCatalog,
    );
  },

  listConnections(params: {
    limit?: number;
    cursor?: string | null;
    q?: string;
  }): Promise<KnowledgeCursorPage<KnowledgeConnection>> {
    return authFetch<unknown>(
      `${base}/connections${queryString({
        limit: params.limit ?? 20,
        cursor: params.cursor,
        q: params.q,
      })}`,
    ).then((value) => projectCursorPage(value, projectKnowledgeConnection));
  },

  createConnection(input: {
    name: string;
    base_url: string;
    credential: string;
  }): Promise<KnowledgeConnection> {
    return authFetch<unknown>(`${base}/connections`, {
      method: "POST",
      body: JSON.stringify({ ...input, operation_id: operationId() }),
    }).then(projectKnowledgeConnection);
  },

  rotateCredential(
    connectionId: string,
    credential: string,
  ): Promise<KnowledgeConnection> {
    return authFetch<unknown>(`${base}/connections/${encodeURIComponent(connectionId)}`, {
      method: "PATCH",
      body: JSON.stringify({ credential, operation_id: operationId() }),
    }).then(projectKnowledgeConnection);
  },

  checkConnection(
    connectionId: string,
  ): Promise<{ status: "passed"; connection: KnowledgeConnection }> {
    return authFetch<unknown>(
      `${base}/connections/${encodeURIComponent(connectionId)}/check`,
      {
        method: "POST",
        body: JSON.stringify({ operation_id: operationId() }),
      },
    ).then((value) => {
      const response = record(value);
      if (response.status !== "passed") throw new Error(INVALID_KNOWLEDGE_RESPONSE);
      return {
        status: "passed" as const,
        connection: projectKnowledgeConnection(response.connection),
      };
    });
  },

  activateConnection(connectionId: string): Promise<{
    connection: KnowledgeConnection;
    sync: KnowledgeCatalogSync;
  }> {
    return authFetch<unknown>(
      `${base}/connections/${encodeURIComponent(connectionId)}/activate-candidate`,
      {
        method: "POST",
        body: JSON.stringify({ operation_id: operationId() }),
      },
    ).then((value) => {
      const response = record(value);
      return {
        connection: projectKnowledgeConnection(response.connection),
        sync: projectKnowledgeSync(response.sync),
      };
    });
  },

  syncConnection(connectionId: string): Promise<KnowledgeCatalogSync> {
    return authFetch<unknown>(
      `${base}/connections/${encodeURIComponent(connectionId)}/syncs`,
      {
        method: "POST",
        body: JSON.stringify({ operation_id: operationId() }),
      },
    ).then(projectKnowledgeSync);
  },

  disableConnection(connectionId: string): Promise<KnowledgeConnection> {
    return authFetch<unknown>(
      `${base}/connections/${encodeURIComponent(connectionId)}/disable`,
      {
        method: "POST",
        body: JSON.stringify({ operation_id: operationId() }),
      },
    ).then(projectKnowledgeConnection);
  },

  listSources(params: {
    limit?: number;
    cursor?: string | null;
    q?: string;
    connectionId?: string;
    status?: string;
  }): Promise<KnowledgeCursorPage<KnowledgeSource>> {
    return authFetch<unknown>(
      `${base}/sources${queryString({
        limit: params.limit ?? 20,
        cursor: params.cursor,
        q: params.q,
        connection_id: params.connectionId,
        status: params.status,
      })}`,
    ).then((value) => projectCursorPage(value, projectKnowledgeSource));
  },

  updateSource(
    sourceId: string,
    input: {
      display_name?: string | null;
      description?: string | null;
      status?: "active" | "disabled";
    },
  ): Promise<KnowledgeSource> {
    return authFetch<unknown>(`${base}/sources/${encodeURIComponent(sourceId)}`, {
      method: "PATCH",
      body: JSON.stringify({ ...input, operation_id: operationId() }),
    }).then(projectKnowledgeSource);
  },

  replaceSourceAcl(
    sourceId: string,
    input: {
      expected_authorization_version: number;
      visibility: "enterprise" | "restricted";
      department_ids: string[];
    },
  ): Promise<KnowledgeSource> {
    return authFetch<unknown>(`${base}/sources/${encodeURIComponent(sourceId)}/acl`, {
      method: "PUT",
      body: JSON.stringify({
        ...input,
        operation_id: operationId(),
      }),
    }).then(projectKnowledgeSource);
  },
};
