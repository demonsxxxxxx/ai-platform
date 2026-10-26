import { Checkbox } from "@lobehub/ui/base-ui";
import { Button, Input } from "@lobehub/ui/base-ui";
import { useState, useEffect, useMemo } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Permission } from "../../types";
import { uuid } from "../../utils/uuid";
import type {
  MCPServerResponse,
  MCPServerCreate,
  MCPRoleQuota,
} from "../../types";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { capabilityDistributionApi, type DepartmentDirectoryNode } from "../../services/api/capabilityDistribution";
import { DepartmentDirectorySelector } from "../panels/DepartmentDirectorySelector";
import { resolveDepartmentSelection } from "../panels/departmentDirectorySelection";
import { validateMcpStaticHeaderNames } from "./mcpHeaderValidation";
import { RoleSelector } from "./RoleSelector";

interface MCPServerFormProps {
  server?: MCPServerResponse | null;
  onSave: (data: MCPServerCreate) => Promise<boolean>;
  onCancel: () => void;
  isLoading?: boolean;
  allowedTransports?: Permission[];
  isSystemServer?: boolean;
}

interface KeyValuePair {
  id: string;
  key: string;
  value: string;
}

type PublicOperationDraft = { tool: string; action: string; object: string; path: string };

function publicOperationDrafts(server?: MCPServerResponse | null): PublicOperationDraft[] {
  return Object.entries(server?.public_operation_summaries ?? {}).map(([tool, value]) => ({
    tool, action: value.action_label, object: value.object_label, path: value.object_path.join("."),
  }));
}

type RoleQuotaDraft = {
  daily_limit: number | "";
  weekly_limit: number | "";
};

function toQuotaDrafts(
  quotas: Record<string, MCPRoleQuota>,
): Record<string, RoleQuotaDraft> {
  return Object.fromEntries(
    Object.entries(quotas).map(([role, quota]) => [
      role,
      {
        daily_limit: quota.daily_limit ?? "",
        weekly_limit: quota.weekly_limit ?? "",
      },
    ]),
  );
}

function serializeRoleQuotas(
  roles: string[],
  drafts: Record<string, RoleQuotaDraft>,
): Record<string, MCPRoleQuota> {
  const quotas: Record<string, MCPRoleQuota> = {};
  for (const role of roles) {
    const quota = drafts[role];
    if (!quota) continue;
    if (quota.daily_limit === "" && quota.weekly_limit === "") continue;
    quotas[role] = {
      daily_limit: quota.daily_limit === "" ? null : quota.daily_limit,
      weekly_limit: quota.weekly_limit === "" ? null : quota.weekly_limit,
    };
  }
  return quotas;
}

export function MCPServerForm({
  server,
  onSave,
  onCancel,
  isLoading = false,
  allowedTransports = [
    Permission.MCP_ADMIN,
    Permission.MCP_WRITE_SSE,
    Permission.MCP_WRITE_HTTP,
  ],
  isSystemServer = false,
}: MCPServerFormProps) {
  const { t } = useTranslation();
  const isEditing = !!server;

  const allTransports: {
    value: MCPServerCreate["transport"];
    label: string;
    permission: Permission;
  }[] = [
    {
      value: "sse",
      label: t("mcp.form.transportSse"),
      permission: Permission.MCP_WRITE_SSE,
    },
    {
      value: "streamable_http",
      label: t("mcp.form.transportHttp"),
      permission: Permission.MCP_WRITE_HTTP,
    },
  ];
  const availableTransports = allTransports.filter((tr) =>
    allowedTransports.includes(tr.permission),
  );

  const defaultTransport = availableTransports[0]?.value ?? "streamable_http";

  const [name, setName] = useState(server?.name ?? "");
  const [transport, setTransport] = useState<MCPServerCreate["transport"]>(
    server?.transport === "sandbox" ? defaultTransport : server?.transport ?? defaultTransport,
  );
  const [enabled, setEnabled] = useState(server?.enabled ?? true);
  const [integrationKind, setIntegrationKind] = useState<"mcp" | "enterprise_gateway">(server?.integration_kind ?? "mcp");

  // HTTP fields
  const [url, setUrl] = useState(server?.url ?? "");
  const [headers, setHeaders] = useState<KeyValuePair[]>(() =>
    Object.entries(server?.headers ?? {}).map(([key, value]) => ({ id: uuid(), key, value })),
  );

  const [allowedRoles, setAllowedRoles] = useState<string[]>(
    server?.allowed_roles ?? [],
  );
  const [allowedDepartments, setAllowedDepartments] = useState<string[]>(server?.allowed_departments ?? []);
  const [departmentDirectory, setDepartmentDirectory] = useState<DepartmentDirectoryNode[] | null>(null);
  const [departmentDirectoryError, setDepartmentDirectoryError] = useState<string | null>(null);
  const departmentSelection = useMemo(
    () => resolveDepartmentSelection(allowedDepartments, departmentDirectory),
    [allowedDepartments, departmentDirectory],
  );
  const [roleQuotas, setRoleQuotas] = useState<Record<string, RoleQuotaDraft>>(
    () => toQuotaDrafts(server?.role_quotas ?? {}),
  );

  const [publicOperations, setPublicOperations] = useState(() => publicOperationDrafts(server));

  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    setDepartmentDirectory(null);
    setDepartmentDirectoryError(null);
    void capabilityDistributionApi.departmentDirectory().then(
      (directory) => { if (active) setDepartmentDirectory(directory); },
      () => { if (active) setDepartmentDirectoryError(t("skills.governance.errors.directoryUnavailable")); },
    );
    return () => { active = false; };
  }, [t]);

  // Update form when server changes
  useEffect(() => {
    if (server) {
      setName(server.name);
      setTransport(server.transport === "sandbox" ? defaultTransport : server.transport);
      setEnabled(server.enabled);
      setIntegrationKind(server.integration_kind ?? "mcp");
      setUrl(server.url ?? "");
      setHeaders(Object.entries(server.headers ?? {}).map(([key, value]) => ({ id: uuid(), key, value })));
      setAllowedRoles(server.allowed_roles ?? []);
      setAllowedDepartments(server.allowed_departments ?? []);
      setRoleQuotas(toQuotaDrafts(server.role_quotas ?? {}));
    } else {
      setName("");
      setTransport(defaultTransport);
      setEnabled(true);
      setIntegrationKind("mcp");
      setUrl("");
      setHeaders([]);
      setAllowedRoles([]);
      setAllowedDepartments([]);
      setRoleQuotas({});
    }
    setPublicOperations(publicOperationDrafts(server));
    setErrors({});
  }, [server, defaultTransport]);

  const handleAllowedRolesChange = (roles: string[]) => {
    setAllowedRoles(roles);
    setRoleQuotas((prev) => {
      const next: Record<string, RoleQuotaDraft> = {};
      for (const role of roles) {
        next[role] = prev[role] ?? { daily_limit: "", weekly_limit: "" };
      }
      return next;
    });
  };

  const updateRoleQuota = (
    role: string,
    field: keyof RoleQuotaDraft,
    value: string,
  ) => {
    if (value !== "") {
      const num = Number(value);
      if (!Number.isInteger(num) || num < 0) return;
    }
    setRoleQuotas((prev) => ({
      ...prev,
      [role]: {
        ...(prev[role] ?? { daily_limit: "", weekly_limit: "" }),
        [field]: value === "" ? "" : Number(value),
      },
    }));
  };

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!name.trim()) {
      newErrors.name = t("mcp.form.validation.nameRequired");
    }

    if (!url.trim()) {
      newErrors.url = t("mcp.form.validation.urlRequired");
    }
    const headerError = validateMcpStaticHeaderNames(headers, integrationKind);
    if (headerError === "mcp_header_conflict") {
      newErrors.headers = t(integrationKind === "enterprise_gateway"
        ? "mcp.form.validation.userIdentityHeaderConflict" : "mcp.form.validation.jwtHeaderConflict");
    } else if (headerError === "mcp_header_duplicate") {
      newErrors.headers = t("mcp.form.validation.duplicateHeader");
    }

    const tools = new Set<string>();
    if (publicOperations.length > 100 || publicOperations.some((row) => {
      const parts = row.path.split(".");
      const invalid = !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,383}$/.test(row.tool) || tools.has(row.tool)
        || !row.action.trim() || row.action.length > 80 || !row.object.trim() || row.object.length > 80
        || parts.length > 8 || parts.some((part) => !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(part)
          || /account|username|userid|email|phone|credential|password|secret|token|path|directory|filename|storage/i.test(part.replaceAll("_", "")));
      tools.add(row.tool);
      return invalid;
    })) newErrors.publicOperations = t("mcp.form.publicSummaryInvalid");
    if (!departmentSelection.authoritative || allowedDepartments.length > 128) {
      newErrors.departments = t("skills.governance.errors.directoryNotAuthoritative");
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!availableTransports.some((option) => option.value === transport) || !validate()) return;

    const data: MCPServerCreate = {
      name: name.trim(),
      transport,
      integration_kind: integrationKind,
      enabled,
      public_operation_summaries: Object.fromEntries(publicOperations.map((row) => [row.tool, {
        action_label: row.action.trim(), object_label: row.object.trim(), object_path: row.path.split("."),
      }])),
      allowed_roles: isSystemServer ? allowedRoles : undefined,
      role_quotas: isSystemServer
        ? serializeRoleQuotas(allowedRoles, roleQuotas)
        : undefined,
      department_ids: allowedDepartments,
    };

    data.url = url.trim();
    if (headers.length > 0) {
      data.headers = headers.reduce(
        (acc, { key, value }) => {
          if (key.trim()) acc[key.trim()] = value;
          return acc;
        },
        {} as Record<string, string>,
      );
    }

    await onSave(data);
  };

  const addHeader = () => {
    setHeaders([...headers, { id: uuid(), key: "", value: "" }]);
  };

  const updateHeader = (id: string, field: "key" | "value", value: string) => {
    setHeaders(
      headers.map((h) => (h.id === id ? { ...h, [field]: value } : h)),
    );
  };

  const removeHeader = (id: string) => {
    setHeaders(headers.filter((h) => h.id !== id));
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Name */}
      <div className="es-field">
        <label className="es-label">{t("mcp.form.serverName")}</label>
        <Input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isEditing}
          placeholder={t("mcp.form.serverNamePlaceholder")}
          className={`enterprise-field-control es-input ${
            errors.name ? "!border-[var(--theme-danger-ring)] dark:!border-[var(--theme-danger)]" : ""
          }`}
        />
        {errors.name && (
          <p className="es-hint text-[var(--theme-danger-text)]">
            {errors.name}
          </p>
        )}
        {isEditing && (
          <p className="es-hint">{t("mcp.form.serverNameUneditable")}</p>
        )}
      </div>

      <div className="es-field">
        <label className="es-label">{t("mcp.form.integrationKind")}</label>
        <LobeSelectField
          aria-label={t("mcp.form.integrationKind")}
          value={integrationKind}
          onChange={value => {
            setIntegrationKind(value as "mcp" | "enterprise_gateway");
            if (value === "enterprise_gateway") setTransport("streamable_http");
          }}
          options={[
            { value: "mcp", label: t("mcp.form.genericMcp") },
            { value: "enterprise_gateway", label: t("mcp.form.enterpriseGateway") },
          ]}
          disabled={isLoading}
        />
        {integrationKind === "enterprise_gateway" && <p className="es-hint">{t("mcp.form.enterpriseGatewayHint")}</p>}
      </div>

      {/* Transport Type */}
      <div className="es-field">
        <label className="es-label">{t("mcp.form.transportType")}</label>
        <LobeSelectField
          aria-label={t("mcp.form.transportType")}
          value={transport}
          onChange={(v) => setTransport(v as MCPServerCreate["transport"])}
          options={availableTransports.map((tr) => ({
            value: tr.value,
            label: tr.label,
          }))}
          disabled={isEditing && server?.transport !== "sandbox"}
        />
        {server?.transport === "sandbox" ? (
          <p className="es-hint" role="status">{t("mcp.form.unsupportedTransport")}</p>
        ) : isEditing && (
          <p className="es-hint">{t("mcp.form.transportUneditable")}</p>
        )}
      </div>

      {/* Enabled */}
      <label className="group flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-[var(--theme-primary-light)]/40">
        <Checkbox
          id="enabled"
          backgroundColor="var(--theme-success)"
          checked={enabled}
          onChange={(checked) => setEnabled(checked)}
        />

        <span className="es-label">{t("mcp.form.enabled")}</span>
      </label>

      {/* Allowed Roles (system servers only) */}
      {isSystemServer && (
        <div className="es-field">
          <label className="es-label">{t("mcp.form.allowedRoles")}</label>
          <p className="es-hint">{t("mcp.form.allowedRolesDescription")}</p>
          <RoleSelector
                    disabled={isLoading}
            selectedRoles={allowedRoles}
            onChange={handleAllowedRolesChange}
          />
          {allowedRoles.length > 0 && (
            <div className="mt-3 space-y-2">
              {allowedRoles.map((role) => {
                const quota = roleQuotas[role] ?? {
                  daily_limit: "",
                  weekly_limit: "",
                };
                return (
                  <div key={role} className="es-section">
                    <div className="text-xs font-medium text-[var(--theme-text-secondary)]">
                      {role}
                    </div>
                    <div className="es-row es-row-2">
                      <div className="es-field">
                        <label className="es-label">
                          {t("mcp.form.dailyLimit")}
                        </label>
                        <Input
                          type="number"
                          min="0"
                          value={quota.daily_limit}
                          onChange={(e) =>
                            updateRoleQuota(role, "daily_limit", e.target.value)
                          }
                          placeholder={t("mcp.form.unlimited")}
                          className="px-3"
                        />
                      </div>
                      <div className="es-field">
                        <label className="es-label">
                          {t("mcp.form.weeklyLimit")}
                        </label>
                        <Input
                          type="number"
                          min="0"
                          value={quota.weekly_limit}
                          onChange={(e) =>
                            updateRoleQuota(
                              role,
                              "weekly_limit",
                              e.target.value,
                            )
                          }
                          placeholder={t("mcp.form.unlimited")}
                          className="px-3"
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="es-field" role="group" aria-label={t("mcp.form.allowedDepartments")}>
        <div className="es-label">{t("mcp.form.allowedDepartments")}</div>
        <p className="es-hint">{t("mcp.form.allowedDepartmentsDescription")}</p>
        <DepartmentDirectorySelector
          directory={departmentDirectory}
          disabled={isLoading}
          loadError={departmentDirectoryError}
          selectedAuthorityIds={allowedDepartments}
          onChange={(ids) => {
            setAllowedDepartments(ids);
            setErrors((current) => ({ ...current, departments: "" }));
          }}
        />
        {errors.departments ? <p className="es-hint text-[var(--theme-danger-text)]" role="alert">{errors.departments}</p> : null}
      </div>

      {isEditing && (
        <p className="es-hint">{t("mcp.form.connectionReentry")}</p>
      )}

      {/* HTTP/SSE connection */}
      <>
          {/* URL field */}
          <div className="es-field">
            <label className="es-label">{t("mcp.form.url")}</label>
            <Input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={t("mcp.form.urlPlaceholder")}
              className={`enterprise-field-control es-input font-mono ${
                errors.url ? "!border-[var(--theme-danger-ring)] dark:!border-[var(--theme-danger)]" : ""
              }`}
            />
            {errors.url && (
              <p className="es-hint text-[var(--theme-danger-text)]">
                {errors.url}
              </p>
            )}
          </div>

          {/* HTTP Headers */}
          <div className="es-field">
            <div className="flex items-center justify-between">
              <label className="es-label">{t("mcp.form.httpHeaders")}</label>
              <Button type="default"
                htmlType="button"
                onClick={addHeader}
                className="text-xs px-2 py-1"
              >
                <Plus size={12} />
                {t("mcp.form.add")}
              </Button>
            </div>
            <div className="space-y-2 mt-1">
              {headers.map((header) => (
                <div key={header.id} className="flex gap-2">
                  <Input
                    type="text"
                    value={header.key}
                    onChange={(e) =>
                      updateHeader(header.id, "key", e.target.value)
                    }
                    placeholder={t("mcp.form.headerNamePlaceholder")}
                    className="font-mono"
                  />
                  <Input
                    type="text"
                    value={header.value}
                    onChange={(e) =>
                      updateHeader(header.id, "value", e.target.value)
                    }
                    placeholder={t("mcp.form.valuePlaceholder")}
                    className="font-mono"
                  />
                  <Button type="text"
                    htmlType="button"
                    onClick={() => removeHeader(header.id)}
                    className="hover:bg-[var(--theme-danger-soft)] hover:text-[var(--theme-danger-text)]"
                    aria-label={t("common.delete")}
                    icon={<Trash2 size={16} />}
                  />
                </div>
              ))}
              {headers.length === 0 && (
                <p className="es-hint italic">{t("mcp.form.noHeaders")}</p>
              )}
              {errors.headers && (
                <p className="es-hint text-[var(--theme-danger-text)]">
                  {errors.headers}
                </p>
              )}
            </div>
          </div>
      </>

      <fieldset className="es-section" data-public-operation-summaries>
        <legend className="es-label">{t("mcp.form.publicSummaryTitle")}</legend>
        <p className="es-hint">{t("mcp.form.publicSummaryDescription")}</p>
        {publicOperations.map((row, index) => (
          <div className="es-row" key={index}>
            {([['tool', t('mcp.form.publicSummaryTool')], ['action', t('mcp.form.publicSummaryAction')], ['object', t('mcp.form.publicSummaryObject')], ['path', t('mcp.form.publicSummaryPath')]] as const).map(([field, label]) => (
              <label className="es-field" key={field}>{label}
                <Input aria-label={`${label} ${index + 1}`} className="enterprise-field-control es-input"
                  value={row[field]} onChange={(event) => setPublicOperations((rows) => rows.map((item, i) => i === index ? { ...item, [field]: event.target.value } : item))} />
              </label>
            ))}
            <Button htmlType="button" type="default" aria-label={`${t("mcp.form.publicSummaryRemove")} ${index + 1}`}
              onClick={() => setPublicOperations((rows) => rows.filter((_, i) => i !== index))}>{t("mcp.form.publicSummaryRemove")}</Button>
          </div>
        ))}
        {errors.publicOperations && <p className="es-hint" role="alert">{errors.publicOperations}</p>}
        <Button htmlType="button" type="default" onClick={() => setPublicOperations((rows) => [...rows, { tool: '', action: '', object: '', path: '' }])}>{t("mcp.form.publicSummaryAdd")}</Button>
      </fieldset>

      {/* Actions */}
      <div className="flex justify-end gap-2 pt-2">
        <Button type="default"
          htmlType="button"
          onClick={onCancel}
          disabled={isLoading}
          className=""
        >
          {t("mcp.form.cancel")}
        </Button>
        <Button type="primary"
          htmlType="submit"
          disabled={isLoading || availableTransports.length === 0}
          className="disabled:opacity-50"
        >
          {isEditing ? t("mcp.form.saveChanges") : t("mcp.form.createServer")}
        </Button>
      </div>
    </form>
  );
}
