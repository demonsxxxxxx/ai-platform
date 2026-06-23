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

  return (
    <section
      data-launchpad-workbench
      className="flex h-full min-h-0 flex-col bg-[var(--theme-bg)] px-3 pb-3 text-slate-950 dark:bg-stone-950 dark:text-stone-100 sm:px-4"
    >
      <div className="shrink-0 border-b border-slate-200/80 py-3 dark:border-stone-800">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold text-slate-900 dark:text-stone-100">
              {t("launchpad.title")}
            </h1>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-stone-400">
              {t("launchpad.subtitle")}
            </p>
          </div>

          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
            <div
              data-launchpad-tab-strip
              className="min-w-0 overflow-x-auto pb-1 sm:pb-0"
            >
              <div
                className={`inline-flex min-w-max p-1 ${workbenchSurface.compactPanel}`}
              >
                {launchpadTabs.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => {
                      setActiveTab(tab.key);
                      setQuery("");
                    }}
                    className={`h-9 min-w-[6.75rem] shrink-0 rounded-md px-4 text-sm font-medium transition-colors ${
                      activeTab === tab.key
                        ? "bg-slate-900 text-white dark:bg-stone-100 dark:text-stone-900"
                        : "text-slate-600 hover:bg-[var(--theme-bg-sidebar)] dark:text-stone-300 dark:hover:bg-stone-800"
                    }`}
                  >
                    {t(`launchpad.tabs.${tab.key}`, tab.label)}
                  </button>
                ))}
              </div>
            </div>

            <label className="relative block w-full sm:w-80">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"
                size={18}
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="panel-search h-10"
                placeholder={t("launchpad.searchPlaceholder")}
                type="search"
              />
            </label>
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-5 py-4">
        <aside className="hidden w-48 shrink-0 overflow-y-auto pr-1 lg:block">
          <div className="sticky top-0 space-y-1">
            {navigationGroups.map((group) => {
              const Icon = groupIcons[group.id] ?? Boxes;
              return (
                <a
                  key={group.id}
                  href={`#${group.id}`}
                  className="flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-600 transition-colors hover:bg-[var(--theme-bg-card)] hover:text-slate-900 dark:text-stone-300 dark:hover:bg-stone-900 dark:hover:text-stone-100"
                >
                  <Icon size={17} />
                  <span className="min-w-0 flex-1 truncate">{group.name}</span>
                  <span className="text-xs text-stone-400">
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
                className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 text-sm text-slate-600 shadow-sm transition-colors hover:border-slate-300 hover:text-slate-900 dark:border-stone-800 dark:bg-stone-900 dark:text-stone-300 dark:hover:border-stone-700 dark:hover:text-stone-100"
              >
                <span className="max-w-28 truncate">{group.name}</span>
                <span className="text-xs text-stone-400">
                  {group.entries.length}
                </span>
              </a>
            ))}
          </nav>

          <div
            data-launchpad-results
            className="mb-3 flex items-center justify-between text-xs text-slate-500 dark:text-stone-400"
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
                className="rounded-md px-2 py-1 hover:bg-[var(--theme-bg-card)] dark:hover:bg-stone-900"
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
                    <h2 className="text-sm font-semibold text-slate-800 dark:text-stone-100">
                      {group.name}
                    </h2>
                    <span className="rounded-md bg-[var(--theme-bg-card)] px-2 py-0.5 text-xs text-slate-500 ring-1 ring-[var(--theme-border)] dark:bg-stone-900 dark:text-stone-300 dark:ring-stone-800">
                      {group.entries.length}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
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
                          className={`group flex min-h-24 flex-col justify-between p-3 transition-[border-color,box-shadow,transform] ${workbenchSurface.compactPanel} ${
                            disabled
                              ? "border-slate-200 opacity-70 dark:border-stone-800"
                              : "cursor-pointer border-slate-200 hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-[0_8px_18px_rgba(18,38,63,0.08)] dark:border-stone-800 dark:hover:border-stone-700"
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
                              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
                              style={{
                                backgroundColor: entry.color
                                  ? `${entry.color}14`
                                  : "rgba(37, 99, 235, 0.08)",
                                color: entry.color || "#1d4ed8",
                              }}
                            >
                              <Icon size={18} />
                            </div>
                            <div className="min-w-0 flex-1">
                              <h3 className="truncate text-sm font-semibold text-slate-900 dark:text-stone-100">
                                {entry.name}
                              </h3>
                              <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500 dark:text-stone-400">
                                {entry.description || entry.groupName}
                              </p>
                            </div>
                          </div>

                          <div className="mt-3 flex items-center justify-between gap-3">
                            <span className="truncate text-xs text-slate-400">
                              {entry.systemKey || entry.url || entry.groupName}
                            </span>
                            {disabled ? (
                              <span className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-500 dark:bg-stone-800 dark:text-stone-400">
                                {destination.reason ||
                                  t("launchpad.unavailable")}
                              </span>
                            ) : (
                              <a
                                href={destination.href}
                                target="_blank"
                                rel="noreferrer"
                                onClick={(event) => event.stopPropagation()}
                                className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:text-stone-300 dark:hover:bg-stone-800 dark:hover:text-stone-100"
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
