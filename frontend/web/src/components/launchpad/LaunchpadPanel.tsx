import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Bot,
  Boxes,
  Building2,
  Cpu,
  Database,
  ExternalLink,
  Globe2,
  Monitor,
  Settings,
} from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import { buildFrontendGovernanceSmokeAttributes } from "../governance/frontendGovernanceState";
import {
  filterLaunchpadGroups,
  getLegacyWebUiFrameUrl,
  launchpadGroups,
  launchpadTabs,
  resolveLaunchpadDestination,
  type LaunchpadEntry,
  type LaunchpadGroup,
  type LaunchpadTabKey,
} from "./catalog";
import { workbenchSurface } from "../workbench/workbenchSurface";

function getEntryIcon(entry: LaunchpadEntry) {
  if (entry.tab === "common") return Globe2;
  if (entry.tab === "ai") return Bot;
  if (entry.systemKey?.includes("Data")) return Database;
  if (entry.systemKey?.includes("Device")) return Monitor;
  if (entry.systemKey?.includes("Admin")) return Settings;
  return Cpu;
}

function countEntries(groups: LaunchpadGroup[]) {
  return groups.reduce((sum, group) => sum + group.entries.length, 0);
}

export function LaunchpadPanel() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<LaunchpadTabKey>("lingxi");
  const [query, setQuery] = useState("");
  const [frameUrl, setFrameUrl] = useState(() => getLegacyWebUiFrameUrl());
  const [frameTitle, setFrameTitle] = useState(() =>
    t("companyNavigation.frame.defaultTitle"),
  );

  const activeGroups = useMemo(
    () => launchpadGroups.filter((group) => group.tab === activeTab),
    [activeTab],
  );
  const searchGroups = query.trim() ? launchpadGroups : activeGroups;
  const visibleGroups = useMemo(
    () => filterLaunchpadGroups(searchGroups, query),
    [searchGroups, query],
  );
  const navigationGroups = query.trim() ? visibleGroups : activeGroups;

  const handlePreview = (entry: LaunchpadEntry) => {
    const destination = resolveLaunchpadDestination(entry);
    if (destination.kind === "url") {
      setFrameUrl(destination.href);
      setFrameTitle(entry.name);
    }
  };

  const openUrl = (href: string) => {
    window.open(href, "_blank", "noopener,noreferrer");
  };

  const tabs = (
    <div
      data-launchpad-tab-strip
      className="min-w-0 overflow-x-auto pb-1 sm:pb-0"
    >
      <div className={`inline-flex min-w-max p-1 ${workbenchSurface.compactPanel}`}>
        {launchpadTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => {
              setActiveTab(tab.key);
              setQuery("");
            }}
            className={`h-10 min-w-[6.75rem] shrink-0 rounded-md px-4 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? "bg-[var(--theme-primary)] text-[var(--theme-primary-foreground)]"
                : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
            }`}
          >
            {t(`launchpad.tabs.${tab.key}`, tab.label)}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <section
      data-company-navigation-shell
      data-launchpad-directory-shell
      data-launchpad-workbench
      {...buildFrontendGovernanceSmokeAttributes("ready")}
      className={workbenchSurface.page}
    >
      <PanelHeader
        title={t("launchpad.title")}
        subtitle={t("launchpad.subtitle")}
        icon={<Building2 size={20} className="text-theme-text-secondary" />}
        actions={tabs}
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder={t("launchpad.searchPlaceholder")}
      />

      <div className={workbenchSurface.catalog.summaryGrid}>
        {launchpadTabs.map((tab) => {
          const groups = launchpadGroups.filter((group) => group.tab === tab.key);
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                setActiveTab(tab.key);
                setQuery("");
              }}
              className={`${workbenchSurface.catalog.summaryCard} flex items-start justify-between gap-3 text-left transition-[border-color,box-shadow] hover:border-[var(--theme-border-strong)] hover:shadow-[0_8px_18px_rgba(18,38,63,0.08)]`}
            >
              <div className="min-w-0">
                <p className={workbenchSurface.catalog.title}>
                  {t(`launchpad.tabs.${tab.key}`, tab.label)}
                </p>
                <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                  {t("launchpad.entriesCount", {
                    count: countEntries(groups),
                  })}
                </p>
              </div>
              <div className={workbenchSurface.catalog.compactIconBox}>
                {tab.key === "ai" ? <Bot size={17} /> : <Boxes size={17} />}
              </div>
            </button>
          );
        })}
      </div>

      <div className="grid min-h-0 flex-1 gap-4 px-4 pb-4 pt-2 xl:grid-cols-[minmax(24rem,0.42fr)_minmax(0,1fr)]">
        <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_4px_12px_rgba(18,38,63,0.03)]">
          <div className="border-b border-[var(--theme-border)] px-4 py-3">
            <div className="flex items-center gap-2">
              <Building2
                size={16}
                className="text-[var(--theme-text-secondary)]"
              />
              <h2 className="text-sm font-semibold text-[var(--theme-text)]">
                {t("companyNavigation.directory.title")}
              </h2>
            </div>
            <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
              {t("companyNavigation.directory.description")}
            </p>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <nav
              aria-label={t("launchpad.groupNavigation")}
              className="mb-3 flex gap-2 overflow-x-auto pb-1 lg:hidden"
            >
              {navigationGroups.map((group) => (
                <a
                  key={group.id}
                  href={`#${group.id}`}
                  className={`${workbenchSurface.catalog.chip} inline-flex h-9 shrink-0 items-center gap-2`}
                >
                  <span className="max-w-28 truncate">{group.name}</span>
                  <span className={`text-xs ${workbenchSurface.catalog.weak}`}>
                    {group.entries.length}
                  </span>
                </a>
              ))}
            </nav>

            <div
              data-launchpad-results
              className={`mb-3 flex items-center justify-between text-xs ${workbenchSurface.catalog.muted}`}
            >
              <span>
                {query
                  ? t("launchpad.searchResults")
                  : t("launchpad.currentCategory")}{" "}
                ·{" "}
                {t("launchpad.entriesCount", {
                  count: countEntries(visibleGroups),
                })}
              </span>
              {query && (
                <button
                  type="button"
                  className="rounded-md px-2 py-1 hover:bg-[var(--theme-workbench-panel)]"
                  onClick={() => setQuery("")}
                >
                  {t("launchpad.clearSearch")}
                </button>
              )}
            </div>

            {visibleGroups.length === 0 ? (
              <div
                className={`flex h-52 items-center justify-center border-dashed text-sm ${workbenchSurface.compactPanel} ${workbenchSurface.mutedText}`}
              >
                {t("launchpad.noResults")}
              </div>
            ) : (
              <div className="space-y-5">
                {visibleGroups.map((group) => (
                  <section id={group.id} key={group.id} className="scroll-mt-4">
                    <div className="mb-2 flex items-center gap-2">
                      <h2 className={workbenchSurface.catalog.title}>
                        {group.name}
                      </h2>
                      <span className={workbenchSurface.catalog.chip}>
                        {group.entries.length}
                      </span>
                    </div>
                    <div className="grid gap-2">
                      {group.entries.map((entry) => {
                        const destination = resolveLaunchpadDestination(entry);
                        const Icon = getEntryIcon(entry);
                        const disabled = destination.kind === "unavailable";
                        const href =
                          destination.kind === "url" ? destination.href : "";
                        return (
                          <article
                            key={entry.id}
                            className={`group flex min-h-[4.5rem] items-center justify-between gap-3 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3 transition-[border-color,box-shadow,transform] ${
                              disabled
                                ? "opacity-70"
                                : "hover:-translate-y-0.5 hover:border-[var(--theme-border-strong)] hover:bg-[var(--theme-workbench-panel)] hover:shadow-[0_8px_18px_rgba(18,38,63,0.08)]"
                            }`}
                          >
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => handlePreview(entry)}
                              className="flex min-w-0 flex-1 items-start gap-3 text-left disabled:cursor-not-allowed"
                              aria-label={
                                disabled
                                  ? undefined
                                  : t("companyNavigation.previewEntry", {
                                      name: entry.name,
                                    })
                              }
                            >
                              <div
                                className={workbenchSurface.catalog.compactIconBox}
                                style={{
                                  backgroundColor: entry.color
                                    ? `${entry.color}14`
                                    : "var(--theme-bg-sidebar)",
                                  color:
                                    entry.color ||
                                    "var(--theme-text-secondary)",
                                }}
                              >
                                <Icon size={18} />
                              </div>
                              <div className="min-w-0 flex-1">
                                <h3 className={`truncate ${workbenchSurface.catalog.title}`}>
                                  {entry.name}
                                </h3>
                                <p className={`mt-1 line-clamp-2 ${workbenchSurface.catalog.body}`}>
                                  {entry.description || entry.groupName}
                                </p>
                              </div>
                            </button>

                            <div className="flex shrink-0 items-center gap-2">
                              {disabled ? (
                                <span className={workbenchSurface.catalog.chip}>
                                  {destination.reason ||
                                    t("launchpad.unavailable")}
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => openUrl(href)}
                                  className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
                                >
                                  {t("launchpad.open")}
                                  <ExternalLink size={14} />
                                </button>
                              )}
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="hidden min-h-0 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_4px_12px_rgba(18,38,63,0.03)] xl:flex xl:flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-[var(--theme-border)] px-4 py-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-[var(--theme-text)]">
                {frameTitle}
              </h2>
              <p className="mt-1 truncate text-xs text-[var(--theme-text-secondary)]">
                {frameUrl}
              </p>
            </div>
            <button
              type="button"
              onClick={() => openUrl(frameUrl)}
              className="btn-secondary h-9 shrink-0"
            >
              <ExternalLink size={15} />
              <span>{t("companyNavigation.frame.openExternal")}</span>
            </button>
          </div>
          <div className="min-h-0 flex-1 bg-white">
            <iframe
              data-legacy-webui-frame
              title={frameTitle}
              src={frameUrl}
              className="h-full w-full border-0 bg-white"
              sandbox="allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts allow-downloads"
              allow="clipboard-read; clipboard-write"
              referrerPolicy="no-referrer-when-downgrade"
            />
          </div>
          <div className="border-t border-[var(--theme-border)] px-4 py-2 text-xs leading-5 text-[var(--theme-text-secondary)]">
            {t("companyNavigation.frame.fallbackHint")}
          </div>
        </div>
      </div>
    </section>
  );
}
