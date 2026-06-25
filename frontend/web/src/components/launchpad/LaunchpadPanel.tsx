import { useMemo, useState } from "react";
import type { ComponentType } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowUpRight,
  Bot,
  Boxes,
  Building2,
  Cpu,
  Database,
  ExternalLink,
  FileText,
  FlaskConical,
  Globe2,
  Monitor,
  Search,
  Settings,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { PanelHeader } from "../common/PanelHeader";
import {
  filterLaunchpadGroups,
  launchpadGroups,
  launchpadTabs,
  resolveLaunchpadDestination,
  type LaunchpadEntry,
  type LaunchpadGroup,
  type LaunchpadTabKey,
} from "./catalog";
import { workbenchSurface } from "../workbench/workbenchSurface";

const groupIcons: Record<string, ComponentType<{ size?: number }>> = {
  "lingxi-RD": FlaskConical,
  "lingxi-PD": Settings,
  "lingxi-AD": Search,
  "lingxi-MFG": Building2,
  "lingxi-PM": Boxes,
  "lingxi-QA": ShieldCheck,
  "lingxi-BD": FileText,
  "lingxi-Admin": Building2,
  "ai-RAG": Bot,
  "ai-Other": Wrench,
  "ai-Intranet": Monitor,
};

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

  const handleOpen = (entry: LaunchpadEntry) => {
    const destination = resolveLaunchpadDestination(entry);
    if (destination.kind === "url") {
      window.open(destination.href, "_blank", "noopener,noreferrer");
    }
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
                ? "bg-[var(--theme-sidebar-panel)] text-white"
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
      data-launchpad-directory-shell
      data-launchpad-workbench
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

      <div className="flex min-h-0 flex-1 gap-5 px-4 pb-4 pt-2">
        <aside className="hidden w-48 shrink-0 overflow-y-auto pr-1 lg:block">
          <div className="sticky top-0 space-y-1">
            {navigationGroups.map((group) => {
              const Icon = groupIcons[group.id] ?? Boxes;
              return (
                <a
                  key={group.id}
                  href={`#${group.id}`}
                  className="flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-workbench-panel)] hover:text-[var(--theme-text)]"
                >
                  <Icon size={17} />
                  <span className="min-w-0 flex-1 truncate">{group.name}</span>
                  <span className={`text-xs ${workbenchSurface.catalog.weak}`}>
                    {group.entries.length}
                  </span>
                </a>
              );
            })}
          </div>
        </aside>

        <div className="min-w-0 flex-1 overflow-y-auto pr-1">
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
                  <div className={workbenchSurface.catalog.cardGrid}>
                    {group.entries.map((entry) => {
                      const destination = resolveLaunchpadDestination(entry);
                      const Icon = getEntryIcon(entry);
                      const disabled = destination.kind === "unavailable";
                      return (
                        <article
                          key={entry.id}
                          role={disabled ? undefined : "button"}
                          tabIndex={disabled ? undefined : 0}
                          aria-label={
                            disabled
                              ? undefined
                              : t("launchpad.openEntry", { name: entry.name })
                          }
                          className={`group flex min-h-28 flex-col justify-between ${workbenchSurface.catalog.entryCard} ${
                            disabled
                              ? "opacity-70"
                              : workbenchSurface.catalog.interactiveEntry
                          }`}
                          onClick={() => {
                            if (!disabled) handleOpen(entry);
                          }}
                          onKeyDown={(event) => {
                            if (
                              disabled ||
                              (event.key !== "Enter" && event.key !== " ")
                            ) {
                              return;
                            }
                            event.preventDefault();
                            handleOpen(entry);
                          }}
                        >
                          <div className="flex items-start gap-3">
                            <div
                              className={workbenchSurface.catalog.compactIconBox}
                              style={{
                                backgroundColor: entry.color
                                  ? `${entry.color}14`
                                  : "var(--theme-bg-sidebar)",
                                color: entry.color || "var(--theme-text-secondary)",
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
                          </div>

                          <div className="mt-3 flex items-center justify-between gap-3">
                            <span className={`truncate text-xs ${workbenchSurface.catalog.weak}`}>
                              {entry.systemKey || entry.url || entry.groupName}
                            </span>
                            {disabled ? (
                              <span className={workbenchSurface.catalog.chip}>
                                {destination.reason ||
                                  t("launchpad.unavailable")}
                              </span>
                            ) : (
                              <a
                                href={destination.href}
                                target="_blank"
                                rel="noreferrer"
                                onClick={(event) => event.stopPropagation()}
                                className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]"
                              >
                                {t("launchpad.open")}
                                {entry.tab === "common" ? (
                                  <ExternalLink size={14} />
                                ) : (
                                  <ArrowUpRight size={14} />
                                )}
                              </a>
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
    </section>
  );
}
