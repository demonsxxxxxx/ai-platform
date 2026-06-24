import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  Search,
  ChevronDown,
  Star,
  X,
  Check,
  LayoutGrid,
  List,
  FolderKanban,
} from "lucide-react";
import { FILE_TYPE_FILTERS, SORT_OPTIONS } from "../constants";
import { useDropdownPos } from "../hooks/useDropdownPos";
import { DropdownShell } from "./DropdownShell";
import { SortIcon } from "./SortIcon";
import type { SortOrder, ViewMode } from "../types";

interface ToolbarProps {
  search: string;
  onSearchChange: (v: string) => void;
  activeFilter: string;
  onFilterChange: (key: string) => void;
  sortBy: string;
  sortOrder: SortOrder;
  onSortChange: (key: string, order: SortOrder) => void;
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  favoritesOnly: boolean;
  onFavoritesToggle: () => void;
  projects: Array<{ id: string; name: string; type: string }>;
  selectedProject: string | null;
  onProjectChange: (id: string | null) => void;
}

/* ── Shared style tokens ──────────────────────────────── */

const btnBase =
  "flex items-center h-9 gap-1.5 rounded-lg border transition-all duration-150 text-sm";
const btnDefault =
  "border-[var(--theme-border)] text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]";
const btnActive =
  "border-[var(--theme-border)] bg-[var(--theme-primary-light)] text-[var(--theme-text)]";

const ddItemBase =
  "w-full text-left px-3 py-2 text-sm transition-colors rounded-lg flex items-center gap-2";
const ddItemActive =
  "text-[var(--theme-text)] bg-[var(--theme-primary-light)] font-medium";
const ddItemDef =
  "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)]";

const smItemBase =
  "w-full text-left px-3 py-2 text-xs rounded-lg flex items-center gap-2";

/* ═══════════════════════════════════════════════════════ */

export function Toolbar({
  search,
  onSearchChange,
  activeFilter,
  onFilterChange,
  sortBy,
  sortOrder,
  onSortChange,
  viewMode,
  onViewModeChange,
  favoritesOnly,
  onFavoritesToggle,
  projects,
  selectedProject,
  onProjectChange,
}: ToolbarProps) {
  const { t } = useTranslation();

  /* Dropdown positions */
  const filterDd = useDropdownPos();
  const sortDd = useDropdownPos();
  const projectDd = useDropdownPos();

  /* Dropdown visibility */
  const [showFilter, setShowFilter] = useState(false);
  const [showSort, setShowSort] = useState(false);
  const [showProject, setShowProject] = useState(false);

  const closeAll = useCallback(() => {
    setShowFilter(false);
    setShowSort(false);
    setShowProject(false);
  }, []);

  /* Derived */
  const currentFilterItem = FILE_TYPE_FILTERS.find(
    (f) => f.key === activeFilter,
  );
  const currentSortLabel = SORT_OPTIONS.find(
    (o) => o.key === sortBy && o.order === sortOrder,
  )?.labelKey;

  const selectedProjectName = selectedProject
    ? selectedProject === "none"
      ? null
      : projects.find((p) => p.id === selectedProject)?.name
    : null;

  return (
    <div className="sticky top-0 z-10">
      {/* Toolbar backdrop */}
      <div
        className="absolute inset-0"
        style={{ backgroundColor: "var(--theme-bg)" }}
      />
      <div className="absolute bottom-0 left-0 right-0 h-px bg-[var(--theme-border)]" />

      <div className="relative px-3 @sm:px-4 @md:px-6 py-2 @md:py-3">
        <div className="flex items-center justify-between gap-2 @sm:gap-3 w-full">
          {/* ─── Left group: Filters ─── */}
          <div className="flex flex-wrap gap-1.5 @sm:gap-2 items-center min-w-0">
            {/* Type filter */}
            <div className="relative">
              <button
                ref={filterDd.ref}
                onClick={() => {
                  closeAll();
                  setShowFilter(true);
                  setTimeout(() => filterDd.update(), 0);
                }}
                className={`${btnBase} ${btnDefault} px-2 @sm:px-2.5`}
              >
                {currentFilterItem?.icon && (
                  <currentFilterItem.icon size={16} />
                )}
                <span className="truncate max-w-[56px] @sm:max-w-none">
                  {t(currentFilterItem?.labelKey || "fileLibrary.types.all")}
                </span>
                <ChevronDown
                  size={16}
                  className={`hidden text-[var(--theme-text-tertiary)] transition-transform duration-200 @sm:block ${
                    showFilter ? "rotate-180" : ""
                  }`}
                />
              </button>
              <DropdownShell
                show={showFilter}
                onClose={() => setShowFilter(false)}
                pos={filterDd.pos}
                align="left"
                w="w-40"
              >
                {FILE_TYPE_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    onClick={() => {
                      onFilterChange(f.key);
                      setShowFilter(false);
                    }}
                    className={`${ddItemBase} ${
                      activeFilter === f.key ? ddItemActive : ddItemDef
                    }`}
                  >
                    {f.icon && <f.icon size={16} />}
                    {t(f.labelKey)}
                  </button>
                ))}
              </DropdownShell>
            </div>

            {/* Favorites */}
            <button
              onClick={onFavoritesToggle}
                  className={`${btnBase} w-9 h-9 p-0 flex items-center justify-center transition-all duration-150 ${
                    favoritesOnly
                      ? "border-amber-300/80 bg-amber-50 text-amber-700 shadow-sm shadow-amber-100 dark:border-amber-600/60 dark:bg-amber-500/10 dark:text-amber-300 dark:shadow-black/20"
                      : btnDefault
                  }`}
            >
              <Star
                size={16}
                className={
                  favoritesOnly ? "fill-amber-400 dark:fill-amber-500" : ""
                }
              />
              <span className="hidden @sm:inline">
                {t("fileLibrary.favorites", "我的收藏")}
              </span>
            </button>

            {/* Project filter */}
            {projects.length > 0 && (
              <div className="relative hidden @lg:block">
                <button
                  ref={projectDd.ref}
                  onClick={() => {
                    closeAll();
                    setShowProject(true);
                    setTimeout(() => projectDd.update(), 0);
                  }}
                  className={`${btnBase} px-2.5 ${
                    selectedProject ? btnActive : btnDefault
                  }`}
                >
                  <FolderKanban size={16} />
                  <span className="max-w-[72px] truncate">
                    {selectedProjectName || t("fileLibrary.projectFilter")}
                  </span>
                  <ChevronDown
                    size={16}
                    className={`text-[var(--theme-text-tertiary)] transition-transform duration-200 ${
                      showProject ? "rotate-180" : ""
                    }`}
                  />
                </button>
                <DropdownShell
                  show={showProject}
                  onClose={() => setShowProject(false)}
                  pos={projectDd.pos}
                  align="right"
                  w="w-48"
                  maxH="max-h-64"
                >
                  <button
                    onClick={() => {
                      onProjectChange(null);
                      setShowProject(false);
                    }}
                    className={`${smItemBase} ${
                      !selectedProject ? ddItemActive : ddItemDef
                    }`}
                  >
                    {t("fileLibrary.allProjects")}
                  </button>
                  <div className="mx-2 my-1 border-t border-[var(--theme-border)]" />
                  {projects.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => {
                        onProjectChange(p.id);
                        setShowProject(false);
                      }}
                      className={`${smItemBase} ${
                        selectedProject === p.id ? ddItemActive : ddItemDef
                      }`}
                    >
                      <span className="truncate">{p.name}</span>
                    </button>
                  ))}
                </DropdownShell>
              </div>
            )}

            {/* Sort */}
            <div className="relative">
              <button
                ref={sortDd.ref}
                onClick={() => {
                  closeAll();
                  setShowSort(true);
                  setTimeout(() => sortDd.update(), 0);
                }}
                className={`${btnBase} gap-1 px-2 @sm:px-2.5 ${btnDefault}`}
              >
                <SortIcon
                  order={sortOrder}
                  className="text-[var(--theme-text-tertiary)]"
                />
                <span className="max-w-[80px] truncate hidden @sm:inline">
                  {t(currentSortLabel ?? "fileLibrary.sort.newest")}
                </span>
                <ChevronDown
                  size={16}
                  className={`text-[var(--theme-text-tertiary)] transition-transform duration-200 ${
                    showSort ? "rotate-180" : ""
                  }`}
                />
              </button>
              <DropdownShell
                show={showSort}
                onClose={() => setShowSort(false)}
                pos={sortDd.pos}
                align="right"
                w="w-40"
              >
                {SORT_OPTIONS.map((o, i) => {
                  const isActive = sortBy === o.key && sortOrder === o.order;
                  return (
                    <div key={`${o.key}-${o.order}`}>
                      {(i === 2 || i === 4) && (
                        <div className="mx-2 my-1 border-t border-[var(--theme-border)]" />
                      )}
                      <button
                        onClick={() => {
                          onSortChange(o.key, o.order);
                          setShowSort(false);
                        }}
                        className={`${smItemBase} ${
                          isActive ? ddItemActive : ddItemDef
                        }`}
                      >
                        <SortIcon
                          order={o.order}
                          className="shrink-0 opacity-60"
                        />
                        <span className="flex-1">{t(o.labelKey)}</span>
                        {isActive && (
                          <Check
                            size={16}
                            className="shrink-0 text-[var(--theme-text-tertiary)]"
                          />
                        )}
                      </button>
                    </div>
                  );
                })}
              </DropdownShell>
            </div>
          </div>

          {/* ─── Right group: Search + View toggle ─── */}
          <div className="flex items-center gap-1.5 @sm:gap-2 shrink-0">
            {/* Search */}
            <div className="group relative flex h-9 w-[120px] items-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-2 pl-9 transition-all duration-150 focus-within:border-[var(--theme-ring)] focus-within:bg-[var(--theme-bg-card)] @sm:w-[160px] @sm:px-3 @md:w-[200px]">
              <Search
                size={16}
                className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[var(--theme-text-tertiary)] @sm:left-3"
              />
              <input
                type="text"
                value={search}
                onChange={(e) => onSearchChange(e.target.value)}
                placeholder={t("fileLibrary.searchPlaceholder")}
                className="h-full min-w-0 flex-1 bg-transparent text-sm text-[var(--theme-text)] placeholder:text-[var(--theme-text-tertiary)] focus:outline-none"
              />
              {search && (
                <button
                  onClick={() => onSearchChange("")}
                  className="shrink-0 rounded text-[var(--theme-text-tertiary)] transition-colors hover:text-[var(--theme-text-secondary)]"
                >
                  <X size={16} />
                </button>
              )}
            </div>

            {/* View toggle */}
            <div className="hidden @sm:block">
              <div className="flex h-9 items-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-px">
                {(["grid", "list"] as const).map((mode) => {
                  const label =
                    mode === "grid"
                      ? t("fileLibrary.gridView")
                      : t("fileLibrary.listView");
                  return (
                    <button
                      key={mode}
                      onClick={() => onViewModeChange(mode)}
                      title={label}
                      className={`relative z-10 flex items-center justify-center w-8 h-full rounded-md transition-colors duration-200 ${
                        viewMode === mode
                          ? "text-stone-800 dark:text-stone-100"
                          : "text-[var(--theme-text-tertiary)] hover:text-[var(--theme-text-secondary)]"
                      }`}
                    >
                      <span className="relative z-10">
                        {mode === "grid" ? (
                          <LayoutGrid size={16} />
                        ) : (
                          <List size={16} />
                        )}
                      </span>
                      {viewMode === mode && (
                        <div className="pointer-events-none absolute inset-0 rounded-md bg-[var(--theme-primary-light)] shadow-sm dark:shadow-black/20" />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
