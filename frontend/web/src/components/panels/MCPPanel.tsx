import { Button, ModalRoot, ModalPortal, ModalBackdrop, ModalPopup, ModalHeader, ModalTitle, ModalContent } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Edit3,
  FolderOpen,
  Plus,
  RefreshCw,
  Search,
  Server,
  ToggleLeft,
  ToggleRight,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "@lobehub/ui/base-ui";
import { PanelHeader } from "../common/PanelHeader";
import { MCPPanelSkeleton } from "../skeletons";
import { Pagination } from "@lobehub/ui/base-ui";
import { ConfirmDialog } from "../common/ConfirmDialog";
import {
  buildFrontendGovernanceSmokeAttributes,
} from "../governance/frontendGovernanceState";
import { WorkbenchStateSurface } from "../workbench/WorkbenchStateSurface";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { useAuth } from "../../hooks/useAuth";
import { useMCP } from "../../hooks/useMcp";
import { Permission } from "../../types";
import { mcpApi } from "../../services/api/mcp";
import type { MCPServerCreate, MCPServerResponse } from "../../types";
import { MCPServerForm } from "../mcp/MCPServerForm";
import { MCPToolDrawer } from "../mcp/MCPToolDrawer";
import { useMcpToolDiscovery } from "../mcp/useMcpToolDiscovery";
import { Table } from "@lobehub/ui/base-ui";
import { canManageMcpLifecycle, isAiAdminUser } from "./capabilityAdmin";
import { resolveMcpGovernanceState } from "./mcpGovernanceState";
import { OrdinaryMcpCatalog } from "./OrdinaryMcpCatalog";

function roleQuotaCount(server: MCPServerResponse): number {
  return Object.values(server.role_quotas ?? {}).filter(Boolean).length;
}

function transportLabel(transport: MCPServerResponse["transport"]): string {
  if (transport === "streamable_http") return "HTTP";
  if (transport === "sandbox") return "Sandbox";
  return "SSE";
}

export function MCPPanel() {
  const { t } = useTranslation();
  const {
    user,
    hasPermission,
    hasAnyPermission,
    isAuthenticated,
    refreshUser,
    isLoading: authLoading,
  } = useAuth();
  const isAiAdmin = isAiAdminUser(user);
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [authorityRevision, setAuthorityRevision] = useState(0);
  const [editorServer, setEditorServer] = useState<MCPServerResponse | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorDetailsLoading, setEditorDetailsLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<MCPServerResponse | null>(null);
  const [toolTarget, setToolTarget] = useState<{ name: string; scope: string } | null>(null);
  const editorPreviousFocusRef = useRef<HTMLElement | null>(null);
  const toolPreviousFocusRef = useRef<HTMLElement | null>(null);
  const editorLoadingRef = useRef(false);
  const canReadMcp = hasPermission(Permission.MCP_READ);
  const pageSize = 20;
  const listParams = useMemo(
    () => ({
      skip: (page - 1) * pageSize,
      limit: pageSize,
      q: searchQuery.trim() || undefined,
    }),
    [page, searchQuery],
  );
  useEffect(() => {
    setPage(1);
  }, [searchQuery]);

  const canManageMcp = canManageMcpLifecycle({
    hasExplicitMcpPermission: hasAnyPermission([
      Permission.MCP_ADMIN,
      Permission.MCP_WRITE_SSE,
      Permission.MCP_WRITE_HTTP,
      Permission.MCP_WRITE_SANDBOX,
      Permission.MCP_DELETE,
    ]),
    isAiAdmin,
  });

  const {
    catalogRequest,
    servers,
    total,
    isLoading,
    error,
    catalogError,
    permissionDenied,
    isAvailable,
    fetchServers,
    getServer,
    createServer,
    updateServer,
    deleteServer,
    toggleServer,
  } = useMCP({
    enabled: isAuthenticated,
    paused: authLoading,
    identityScope: JSON.stringify([user?.tenant_id, user?.id, user?.roles, user?.permissions, user?.is_admin, user?.department_id, authorityRevision]),
    listParams,
    allAuthorizedCatalog: !isAiAdmin,
    canManageServers: canManageMcp,
  });
  editorLoadingRef.current = isLoading;
  const retryCatalog = async () => {
    if (!permissionDenied) {
      await fetchServers();
      return;
    }
    // A directory read alone cannot prove lifecycle authority. Only a
    // completed principal refresh admits a new permission-checking read.
    const outcome = await refreshUser();
    if (outcome.status === "completed") {
      setAuthorityRevision((revision) => revision + 1);
    }
  };

  const directoryIsLoading =
    authLoading || (isLoading && servers.length === 0 && !editorOpen);
  const mcpGovernance = resolveMcpGovernanceState({
    isAuthenticated,
    isLoading: directoryIsLoading,
    canReadMcp,
    canManageMcp,
    servers,
    total,
    loadError: catalogError,
    permissionDenied,
  });
  const lifecycleAvailability = mcpGovernance.lifecycleAvailability;
  const canManageMcpUi = canManageMcp && isAvailable;
  const discoveryEnabled = isAuthenticated && !authLoading && !isLoading && isAvailable && !catalogError;
  const discovery = useMcpToolDiscovery(servers, JSON.stringify(catalogRequest), discoveryEnabled,
    isAiAdmin ? mcpApi.getServerCatalog : mcpApi.discoverTools);
  const toolsScope = JSON.stringify([catalogRequest, authLoading, permissionDenied, isAvailable]);
  const selectedToolServer = toolTarget?.scope === toolsScope ? servers.find((server) => server.name === toolTarget.name) : undefined;
  useEffect(() => {
    setToolTarget((target) => target?.scope === toolsScope ? target : null);
  }, [toolsScope]);

  const openEditor = async (server: MCPServerResponse | null) => {
    if (!canManageMcpUi) return;
    editorPreviousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    if (server) {
      setEditorDetailsLoading(true);
      const detailedServer = await getServer(server.name);
      setEditorDetailsLoading(false);
      if (!detailedServer) return;
      setEditorServer(detailedServer);
    } else {
      setEditorServer(null);
    }
    setToolTarget(null);
    setEditorOpen(true);
  };

  const openCreate = () => {
    void openEditor(null);
  };

  const closeEditor = () => {
    if (!editorLoadingRef.current) setEditorOpen(false);
  };

  useEffect(() => {
    if (canManageMcpUi) return;
    setEditorOpen(false);
    setEditorServer(null);
    setDeleteTarget(null);
  }, [canManageMcpUi]);


  const saveServer = async (data: MCPServerCreate): Promise<boolean> => {
    const saved = editorServer
      ? await updateServer(editorServer.name, data, editorServer.is_system)
      : await createServer(data, true);
    if (saved) {
      setEditorOpen(false);
      toast.success(t(editorServer ? "mcp.admin.updateSuccess" : "mcp.admin.createSuccess"));
    }
    return Boolean(saved);
  };

  const confirmDelete = async () => {
    if (!canManageMcpUi || !deleteTarget) return;
    const deleted = await deleteServer(deleteTarget.name, deleteTarget.is_system);
    if (deleted) {
      setDeleteTarget(null);
      toast.success(t("mcp.admin.deleteSuccess"));
    }
  };

  if (!isAiAdmin) {
    return (
      <OrdinaryMcpCatalog
        servers={servers}
        isLoading={directoryIsLoading}
        listError={catalogError}
        discovery={discovery}
        onRetry={retryCatalog}
      />
    );
  }

  if (mcpGovernance.pageState === "loading") {
    return (
      <div
        data-phase1c-surface="mcp"
        data-mcp-directory-shell
        {...buildFrontendGovernanceSmokeAttributes(mcpGovernance.pageState)}
        data-required-permission={mcpGovernance.requiredPermission}
        data-auth-projection-has-permission={
          mcpGovernance.authProjectionHasPermission
        }
        className={workbenchSurface.statePage}
      >
        <MCPPanelSkeleton />
      </div>
    );
  }

  if (
    mcpGovernance.pageState === "logged-out" ||
    mcpGovernance.pageState === "no-workspace" ||
    mcpGovernance.pageState === "forbidden"
  ) {
    return (
      <div
        data-phase1c-surface="mcp"
        data-mcp-directory-shell
        {...buildFrontendGovernanceSmokeAttributes(mcpGovernance.pageState)}
        data-required-permission={mcpGovernance.requiredPermission}
        data-auth-projection-has-permission={
          mcpGovernance.authProjectionHasPermission
        }
        className={workbenchSurface.statePage}
      >
        <WorkbenchStateSurface
          state={mcpGovernance.pageState}
          surface="mcp-directory"
          title={
            mcpGovernance.pageState === "forbidden"
              ? t("mcp.noPermission")
              : undefined
          }
          description={
            mcpGovernance.pageState === "forbidden"
              ? t("mcp.catalogUnavailable.description")
              : undefined
          }
          actions={mcpGovernance.pageState === "forbidden" ? (
            <Button type="default" htmlType="button" className="" data-mcp-retry onClick={retryCatalog}>
              {t("mcp.catalogUnavailable.retry")}
            </Button>
          ) : undefined}
          capabilities={[
            {
              title: t("mcp.permissionLimited.title"),
              description: t("mcp.permissionLimited.description"),
              state: mcpGovernance.directoryAvailability.state,
              labelKey: mcpGovernance.directoryAvailability.labelKey,
            },
            {
              title: t("mcp.lifecycleGovernance.title"),
              description: t("mcp.lifecycleGovernance.description"),
              state: lifecycleAvailability.state,
              labelKey: lifecycleAvailability.labelKey,
            },
          ]}
        />
      </div>
    );
  }

  return (
    <div
      data-phase1c-surface="mcp"
      data-mcp-directory-shell
      {...buildFrontendGovernanceSmokeAttributes(mcpGovernance.pageState)}
      data-required-permission={mcpGovernance.requiredPermission}
      data-auth-projection-has-permission={
        mcpGovernance.authProjectionHasPermission
      }
      className={workbenchSurface.page}
    >
      <PanelHeader
        title={t("mcp.title")}
        subtitle={t("mcp.subtitle")}
        icon={<Server size={20} className="text-theme-text-secondary" />}
        searchValue={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t("mcp.searchPlaceholder")}
        searchAccessory={<>
          <Button type="default" htmlType="button" className="inline-flex min-h-11 items-center gap-2" data-mcp-refresh-tools onClick={discovery.refresh} disabled={!discoveryEnabled || Array.from(discovery.statusByServer.values()).includes("loading")} aria-label={t("mcp.tools.refresh")} title={t("mcp.tools.refresh")}>
            <RefreshCw size={16} /><span className="hidden sm:inline">{t("mcp.tools.refresh")}</span>
          </Button>
          {canManageMcpUi ? <div data-mcp-admin-controls>
            <Button type="primary" htmlType="button" onClick={openCreate} className="inline-flex min-h-11 items-center justify-center gap-2" aria-label={t("mcp.admin.addServer")} title={t("mcp.admin.addServer")}>
              <Plus size={18} /><span className="hidden sm:inline">{t("mcp.admin.addServer")}</span>
            </Button>
          </div> : null}
        </>}
      />
      {error && (
        <div className="mx-4 mt-4 flex items-start gap-2 rounded-lg bg-[var(--theme-danger-soft)] p-3 text-sm text-[var(--theme-danger)] ring-1 ring-[var(--theme-danger-ring)]">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{t("mcp.admin.operationFailed")}</span>
        </div>
      )}
      {mcpGovernance.pageState === "degraded" ? (
        <div className="px-4 pb-2 pt-1">
          <WorkbenchStateSurface
            state="degraded"
            surface="mcp-directory"
            title={t("mcp.catalogUnavailable.title")}
            description={t("mcp.catalogUnavailable.description")}
            actions={<Button type="default" htmlType="button" className="" data-mcp-retry onClick={retryCatalog}>{t("mcp.catalogUnavailable.retry")}</Button>}
            className="max-w-none text-left"
          />
        </div>
      ) : null}

      <div className={workbenchSurface.catalog.content}>
        {servers.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-theme-text-secondary">
            {searchQuery ? (
              <Search size={42} className="mb-3 text-theme-text-secondary" />
            ) : (
              <FolderOpen size={42} className="mb-3 text-theme-text-secondary" />
            )}
            <p className="text-center text-sm">
              {mcpGovernance.governedUnavailable
                ? t("mcp.catalogUnavailable.title")
                : searchQuery
                ? t("mcp.noMatchingServers")
                : t("mcp.noServers")}
            </p>
            {mcpGovernance.governedUnavailable ? (
              <p className="mt-2 max-w-md text-center text-xs leading-5 text-[var(--theme-text-secondary)]">
                {t("mcp.catalogUnavailable.description")}
              </p>
            ) : null}
            {canManageMcpUi && !searchQuery ? (
              <Button type="primary"
                htmlType="button"
                onClick={openCreate}
                className="mt-4 inline-flex min-h-11 items-center gap-2"
              >
                <Plus size={18} />
                {t("mcp.admin.addFirstServer")}
              </Button>
            ) : null}
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)]">
            <div aria-label={t("mcp.title")}><Table
 size="small" pagination={false}
 dataSource={servers}
 rowKey={(server) => server.name}
onRow={(server) => {  return ({ role: "row", "data-mcp-server-row": server.name }); }}
 columns={[
{ key: "name", title: t("mcp.form.serverName"),
render: (_value, server) => (<>
<p className="break-words font-semibold">{server.name}</p>
                      <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{t(server.is_system ? "mcp.card.system" : "mcp.card.user")}</p>
</>) },
{ key: "toolCount", title: t("mcp.tools.catalogCount"),
onCell: () => ({"data-mcp-tool-count": true}),
render: (_value, server) =>  {
const status = discovery.statusByServer.get(server.name) ?? "idle";
return (<>
{status === "ready" ? t("mcp.tools.count", { count: discovery.toolsByServer.get(server.name)?.filter((tool) => tool.available !== false).length ?? 0 }) : "—"}
</>);
} },
{ key: "enabled", title: t("mcp.card.statusLabel"),
render: (_value, server) => (<>
<span className={server.enabled ? "text-[var(--theme-accent)]" : "text-[var(--theme-text-secondary)]"}>{t(server.enabled ? "mcp.card.statusEnabled" : "mcp.card.statusDisabled")}</span>
</>) },
{ key: "discovery", title: t("mcp.tools.discoveryStatus"),
render: (_value, server) =>  {
const status = discovery.statusByServer.get(server.name) ?? "idle";
return (<>
<span className={status === "unavailable" ? "text-[var(--theme-danger)]" : "text-[var(--theme-text-secondary)]"}>{t(`mcp.discovery.${status}`)}</span>
</>);
} },
{ key: "transport", title: t("mcp.form.transportType"),
render: (_value, server) => (<>
{transportLabel(server.transport)}
</>) },
{ key: "scope", title: t("mcp.directory.accessScope"),
render: (_value, server) =>  {
const quotaCount = roleQuotaCount(server);
return (<>
<p title={server.allowed_departments?.join("、")}>{server.allowed_departments?.length ? t("mcp.directory.departments", { count: server.allowed_departments.length }) : t("mcp.directory.allDepartments")}</p>
                      <p className="mt-1 break-words text-xs text-[var(--theme-text-secondary)]">{t("mcp.directory.roles", { roles: server.allowed_roles?.length ? server.allowed_roles.join("、") : t("mcp.directory.allRoles") })}</p>
                      {quotaCount > 0 ? <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{t("mcp.card.roleQuotaCount")}：{quotaCount}</p> : null}
</>);
} },
{ key: "actions", title: t("mcp.directory.actions"),
render: (_value, server) => (<>
<div className="flex items-center gap-1">
                        <Button type="default" htmlType="button" className="min-h-11 whitespace-nowrap" data-mcp-view-tools onClick={(event) => { toolPreviousFocusRef.current = event.currentTarget; setToolTarget({ name: server.name, scope: toolsScope }); }}>{t("mcp.tools.view")}</Button>
                        {canManageMcpUi ? <div className="flex items-center gap-1" data-mcp-admin-controls>
                          <Button type="text" htmlType="button" data-mcp-admin-toggle onClick={async () => { const updated = await toggleServer(server.name); if (updated) toast.success(t(updated.enabled ? "mcp.admin.enableSuccess" : "mcp.admin.disableSuccess")); }} className="btn-icon min-h-11 min-w-11" aria-label={server.enabled ? t("mcp.admin.disableServer") : t("mcp.admin.enableServer")} title={server.enabled ? t("mcp.admin.disableServer") : t("mcp.admin.enableServer")} disabled={isLoading}>
                            {server.enabled ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                          </Button>
                          <Button type="text" htmlType="button" data-mcp-admin-edit onClick={() => void openEditor(server)} className="btn-icon min-h-11 min-w-11" aria-label={t("mcp.admin.editServer")} title={t("mcp.admin.editServer")} disabled={isLoading || editorDetailsLoading}><Edit3 size={18} /></Button>
                          <Button type="text" htmlType="button" data-mcp-admin-delete onClick={() => { setToolTarget(null); setDeleteTarget(server); }} className="btn-icon min-h-11 min-w-11 hover:bg-[var(--theme-danger-soft)] hover:text-[var(--theme-danger)]" aria-label={t("mcp.admin.deleteServer")} title={t("mcp.admin.deleteServer")} disabled={isLoading}><Trash2 size={18} /></Button>
                        </div> : null}
                      </div>
</>) }
]}
/></div>
          </div>
        )}
      </div>

      {total > pageSize && (
        <div className="enterprise-divider border-t px-3 py-3 sm:px-4">
          <Pagination
                  current={page} hideOnSinglePage responsive showSizeChanger={false}
                  showTotal={(total, [start, end]) => `${start}-${end} / ${total}`}
            pageSize={pageSize}
            total={total}
            onChange={setPage}
          />
        </div>
      )}
      {selectedToolServer ? <MCPToolDrawer returnFocusRef={toolPreviousFocusRef} key={selectedToolServer.name} serverName={selectedToolServer.name} fullCatalog={selectedToolServer.integration_kind === "enterprise_gateway"} status={discovery.statusByServer.get(selectedToolServer.name) ?? "idle"} tools={discovery.toolsByServer.get(selectedToolServer.name) ?? []} onRefresh={discovery.refresh} onClose={() => setToolTarget(null)} /> : null}
      {canManageMcpUi ? (
        <ModalRoot open={editorOpen} onOpenChange={(open) => { if (!open) closeEditor(); }}>
          <ModalPortal>
            <ModalBackdrop />
            <ModalPopup width="min(42rem, calc(100vw - 2rem))" finalFocus={editorPreviousFocusRef}>
              <ModalHeader>
                <ModalTitle>{editorServer ? t("mcp.admin.editServer") : t("mcp.admin.addServer")}</ModalTitle>
                <Button type="text" data-mcp-editor-initial-focus autoFocus onClick={closeEditor} aria-label={t("common.close")} disabled={isLoading}><X size={18} /></Button>
              </ModalHeader>
              <ModalContent>
                {error ? <p role="alert" className="mb-3 text-sm text-[var(--theme-danger)]">{t("mcp.admin.operationFailed")}</p> : null}
                <MCPServerForm server={editorServer} onSave={saveServer} onCancel={closeEditor} isLoading={isLoading} isSystemServer />
              </ModalContent>
            </ModalPopup>
          </ModalPortal>
        </ModalRoot>
      ) : null}
      <ConfirmDialog
        isOpen={canManageMcpUi && Boolean(deleteTarget)}
        title={t("mcp.admin.deleteServer")}
        message={t("mcp.admin.deleteConfirmation", { name: deleteTarget?.name ?? "" })}
        confirmText={t("mcp.admin.deleteServer")}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        loading={isLoading}
      />
    </div>
  );
}
