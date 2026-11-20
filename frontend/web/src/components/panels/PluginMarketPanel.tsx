import { Button } from "@lobehub/ui/base-ui";
import { KeyRound, Plug, RefreshCw, SearchX, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../hooks/useAuth";
import { profileDriveApi, PROFILE_DRIVE_CONNECTION_CHANGED_EVENT } from "../../services/api/profileDrive";
import { PanelHeader } from "../common/PanelHeader";
import { usePluginCatalog } from "../mcp/usePluginCatalog";
import { useMcpToolDiscovery } from "../mcp/useMcpToolDiscovery";
import { MCPToolDrawer } from "../mcp/MCPToolDrawer";
import { ProfileDriveAuthenticationDialog } from "../profile-drive/ProfileDriveAuthenticationDialog";
import { workbenchSurface } from "../workbench/workbenchSurface";

/** System-distributed plugins: installation is a projection, never a user write. */
export function PluginMarketPanel() {
  const { t } = useTranslation();
  const { user, isAuthenticated, isLoading: authLoading } = useAuth();
  const scope = JSON.stringify([user?.tenant_id, user?.id, user?.roles, user?.permissions, user?.is_admin,
    user?.department_id, user?.direct_department_ids, user?.department_ids, user?.department_authority_status, authLoading]);
  const catalog = usePluginCatalog(scope, isAuthenticated && !authLoading);
  const discovery = useMcpToolDiscovery(catalog.plugins, scope, !catalog.loading && !catalog.failed && !authLoading);
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<{ name: string; scope: string } | null>(null);
  const [authScope, setAuthScope] = useState<string | null>(null);
  const [connection, setConnection] = useState<{ scope: string; connected: boolean } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const hasDrive = catalog.plugins.some(plugin => plugin.user_authorization === "profile_drive");
  useEffect(() => {
    let active = true;
    const refresh = () => {
      if (!hasDrive || authLoading) return;
      void profileDriveApi.status().then(status => {
        if (active) setConnection({ scope, connected: status.connected });
      }).catch(() => { if (active) setConnection(null); });
    };
    refresh();
    window.addEventListener(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT, refresh);
    return () => { active = false; window.removeEventListener(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT, refresh); };
  }, [hasDrive, scope, authLoading]);
  const visible = catalog.plugins.filter(plugin =>
    `${plugin.display_name} ${plugin.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const selected = detail?.scope === scope ? catalog.plugins.find(plugin => plugin.name === detail.name && plugin.integration_kind !== "enterprise_gateway") : undefined;

  return (
    <div data-plugin-market className={workbenchSurface.page}>
      <PanelHeader title={t("plugins.title")} subtitle={t("plugins.subtitle")} icon={<Plug size={20} />}
        searchValue={query} onSearchChange={setQuery} searchPlaceholder={t("plugins.search")}
        searchAccessory={<Button htmlType="button" type="default" size="large" icon={<RefreshCw size={16} />}
          onClick={() => void catalog.refresh()} disabled={catalog.loading || authLoading}>{t("common.refresh")}</Button>} />
      <div className={workbenchSurface.catalog.content}>
        {catalog.loading || authLoading ? <p role="status">{t("plugins.loading")}</p> : catalog.failed ? (
          <div role="alert" className={workbenchSurface.catalog.emptyState}>
            <p>{t("plugins.failed")}</p>
            <Button htmlType="button" type="default" size="large" onClick={() => void catalog.refresh()}>{t("plugins.retry")}</Button>
          </div>
        ) : visible.length === 0 ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <SearchX size={24} /><h2 className={workbenchSurface.catalog.emptyTitle}>{t(query ? "plugins.noMatches" : "plugins.empty")}</h2>
            {query ? <Button htmlType="button" type="default" size="large" onClick={() => setQuery("")}>{t("plugins.clearSearch")}</Button> : null}
          </div>
        ) : <div className={workbenchSurface.catalog.cardGrid}>
          {visible.map(plugin => {
            const status = discovery.statusByServer.get(plugin.name) ?? "idle";
            const driveConnected = connection?.scope === scope && connection.connected;
            const needsAuth = discovery.reasonsByServer.get(plugin.name) === "authorization_required" ||
              (plugin.user_authorization === "profile_drive" && connection?.scope === scope && !connection.connected);
            const label = needsAuth ? "authorization_required" : status === "gateway_managed" ? "gateway_managed" : status === "ready" ? "ready" : status === "unavailable" ? "unavailable" : "checking";
            return <article data-plugin-card={plugin.name} key={plugin.name} className={`${workbenchSurface.catalog.entryCard} flex min-w-0 flex-col gap-4`}>
              <div className="flex items-start gap-3">
                <Plug size={24} className="shrink-0 text-[var(--theme-text-secondary)]" />
                <div className="min-w-0 flex-1"><h2 className="break-words text-base font-semibold">{plugin.display_name}</h2>
                  <span className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--theme-text-secondary)]"><ShieldCheck size={14} />{t("plugins.preinstalled")}</span>
                </div>
              </div>
              {plugin.description ? <p className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--theme-text-secondary)]">{plugin.description}</p> : null}
              <p role="status" className={`text-sm ${label === "ready" ? "text-[var(--theme-success-text)]" : label === "unavailable" ? "text-[var(--theme-danger-text)]" : "text-[var(--theme-text-secondary)]"}`}>
                {t(`plugins.status.${label}`)}{status === "ready" && !needsAuth ? ` · ${t("mcp.tools.count", { count: discovery.toolsByServer.get(plugin.name)?.length ?? 0 })}` : ""}
              </p>
              <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-[var(--theme-border)] pt-3">
                {plugin.integration_kind !== "enterprise_gateway" ? <Button htmlType="button" type="default" size="large" onClick={event => { detailRef.current = event.currentTarget; setDetail({ name: plugin.name, scope }); }}>{t("plugins.details")}</Button> : null}
                {plugin.user_authorization === "profile_drive" ? <Button htmlType="button" type="default" size="large" icon={<KeyRound size={16} />}
                  onClick={event => { if (event.currentTarget instanceof HTMLButtonElement) triggerRef.current = event.currentTarget; setAuthScope(scope); }}>{t(driveConnected ? "plugins.reauthorize" : "plugins.authorize")}</Button> : null}
                {needsAuth && plugin.user_authorization !== "profile_drive" ? <span className="text-xs text-[var(--theme-text-secondary)]">{t("plugins.loginAgain")}</span> : null}
              </div>
            </article>;
          })}
        </div>}
      </div>
      {selected ? <MCPToolDrawer key={selected.name} serverName={selected.display_name}
        status={discovery.statusByServer.get(selected.name) ?? "idle"} tools={discovery.toolsByServer.get(selected.name) ?? []}
        onRefresh={discovery.refresh} onClose={() => setDetail(null)} returnFocusRef={detailRef} /> : null}
      {authScope === scope && hasDrive ? <ProfileDriveAuthenticationDialog open returnFocusRef={triggerRef}
        onClose={() => setAuthScope(null)} onAuthenticated={() => { setConnection({ scope, connected: true }); discovery.refresh(); }} /> : null}
    </div>
  );
}
