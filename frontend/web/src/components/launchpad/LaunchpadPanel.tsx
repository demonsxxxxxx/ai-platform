import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";
import {
  BookMarked,
  BookOpen,
  Building2,
  ChevronRight,
  Database,
  FileSearch,
  FlaskConical,
  Globe2,
  Grid2X2,
  Languages,
  Landmark,
  Link2,
  Palette,
  Search,
  SearchX,
  Sparkles,
  Star,
  TrendingUp,
  X,
  type LucideIcon,
} from "lucide-react";

import { useAuth } from "../../hooks/useAuth";
import { authApi } from "../../services/api/auth";
import { buildFrontendGovernanceSmokeAttributes } from "../governance/frontendGovernanceState";
import { workbenchSurface } from "../workbench/workbenchSurface";
import {
  filterLaunchpadGroups,
  getLaunchpadIconUrl,
  launchpadGroups,
  type LaunchpadEntry,
} from "./catalog";
import {
  LAUNCHPAD_FAVORITES_METADATA_KEY,
  parseLaunchpadFavoriteIds,
} from "./favorites";

const categoryIcons: Record<string, LucideIcon> = {
  内网登录: Building2,
  AI: Sparkles,
  翻译: Languages,
  绘图: Palette,
  文献检索: Search,
  文献期刊: BookOpen,
  专利检索: FileSearch,
  药物蛋白数据库: Database,
  预测工具: FlaskConical,
  中国药监机构或协会: Landmark,
  国外药监机构或协会: Globe2,
  药典查询: BookMarked,
  财经资讯: TrendingUp,
};

const categoryTones = [
  "#0f8a83",
  "#3568c0",
  "#8a4aa2",
  "#c55b48",
  "#5f7f35",
  "#a56814",
];

const allEntries = launchpadGroups.flatMap((group) => group.entries);
const entryIds = new Set(allEntries.map((entry) => entry.id));
const entriesById = new Map(allEntries.map((entry) => [entry.id, entry]));

interface DirectorySectionProps {
  id: string;
  title: string;
  entries: LaunchpadEntry[];
  icon: LucideIcon;
  tone: string;
  favoriteIds: ReadonlySet<string>;
  favoritesDisabled: boolean;
  onToggleFavorite: (entryId: string) => void;
}

function DirectorySection({
  id,
  title,
  entries,
  icon: SectionIcon,
  tone,
  favoriteIds,
  favoritesDisabled,
  onToggleFavorite,
}: DirectorySectionProps) {
  const { t } = useTranslation();

  return (
    <section
      id={id}
      className="scroll-mt-5 rounded-2xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-3 shadow-[var(--theme-shadow-sm)] sm:p-4"
      style={{ "--section-tone": tone } as CSSProperties}
    >
      <header className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className="flex size-8 shrink-0 items-center justify-center rounded-lg"
            style={{ color: tone, backgroundColor: `${tone}16` }}
          >
            <SectionIcon aria-hidden="true" className="size-4" />
          </span>
          <h2 className="truncate text-sm font-semibold text-[var(--theme-text)] sm:text-[15px]">
            {title}
          </h2>
          <span className="font-mono text-[10px] tabular-nums text-[var(--theme-text-secondary)]">
            {entries.length}
          </span>
        </div>
      </header>

      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {entries.map((entry) => {
          const isFavorite = favoriteIds.has(entry.id);
          const isFeaturedPlatform = entry.id === "内网登录:灵犀平台";

          return (
            <div
              key={entry.id}
              data-launchpad-entry
              className="group flex min-h-[68px] min-w-0 items-center overflow-hidden rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] transition-[border-color,box-shadow] hover:border-[var(--section-tone)] hover:shadow-sm"
            >
              <a
                href={entry.url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={t("companyNavigation.openEntry", {
                  name: entry.name,
                })}
                className="flex min-w-0 flex-1 items-center gap-3 py-2.5 pl-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--theme-ring)]"
              >
                <span
                  className={`relative flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-[10px] border border-[var(--theme-border)] bg-white shadow-sm ${
                    isFeaturedPlatform
                      ? "motion-safe:animate-pulse shadow-[0_0_14px_rgba(139,92,246,0.42)]"
                      : ""
                  }`}
                >
                  <Globe2
                    aria-hidden="true"
                    className="size-4 text-[var(--theme-text-secondary)]"
                  />
                  <img
                    src={getLaunchpadIconUrl(entry.icon)}
                    alt=""
                    className="absolute inset-0 size-full bg-white object-contain"
                    loading="lazy"
                    onError={(event) => {
                      event.currentTarget.hidden = true;
                    }}
                  />
                </span>

                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-semibold text-[var(--theme-text)] sm:text-sm">
                    {entry.name}
                  </span>
                  <span
                    className="mt-0.5 block truncate text-[11px] text-[var(--theme-text-secondary)] sm:text-xs"
                    title={entry.description || undefined}
                  >
                    {entry.description || t("launchpad.visitWebsite")}
                  </span>
                </span>

                <ChevronRight
                  aria-hidden="true"
                  className="size-4 shrink-0 text-[var(--theme-text-tertiary)] transition-transform group-hover:translate-x-0.5"
                />
              </a>

              <button
                type="button"
                aria-label={t(
                  isFavorite
                    ? "launchpad.removeFavorite"
                    : "launchpad.addFavorite",
                  { name: entry.name },
                )}
                aria-pressed={isFavorite}
                disabled={favoritesDisabled}
                onClick={() => onToggleFavorite(entry.id)}
                className={`mr-1 flex size-9 shrink-0 items-center justify-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)] disabled:cursor-wait disabled:opacity-50 ${
                  isFavorite
                    ? "text-amber-500 hover:bg-amber-500/10"
                    : "text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-hover)] hover:text-amber-500"
                }`}
              >
                <Star
                  aria-hidden="true"
                  className="size-4"
                  fill={isFavorite ? "currentColor" : "none"}
                />
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function LaunchpadPanel() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const currentUserIdRef = useRef(user?.id);
  currentUserIdRef.current = user?.id;
  const [query, setQuery] = useState("");
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [favoritesOwnerId, setFavoritesOwnerId] = useState<string | null>(null);
  const [favoritesReady, setFavoritesReady] = useState(false);
  const [favoritesSaving, setFavoritesSaving] = useState(false);

  useEffect(() => {
    const requestOwnerId = user?.id;
    setFavoritesOwnerId(null);
    setFavoritesReady(false);
    setFavoritesSaving(false);
    if (!requestOwnerId) {
      setFavoriteIds([]);
      return;
    }

    const controller = new AbortController();
    authApi
      .getProfile({ signal: controller.signal })
      .then((profile) => {
        if (currentUserIdRef.current !== requestOwnerId) return;
        setFavoriteIds(
          parseLaunchpadFavoriteIds(
            profile.metadata?.[LAUNCHPAD_FAVORITES_METADATA_KEY],
            entryIds,
          ),
        );
        setFavoritesOwnerId(requestOwnerId);
        setFavoritesReady(true);
      })
      .catch(() => {
        if (
          !controller.signal.aborted &&
          currentUserIdRef.current === requestOwnerId
        ) {
          setFavoriteIds([]);
          toast.error(t("launchpad.favoriteLoadFailed"));
        }
      });
    return () => controller.abort();
  }, [t, user?.id]);

  const favoritesBelongToCurrentUser =
    Boolean(user?.id) && favoritesOwnerId === user?.id;
  const favoriteIdSet = useMemo(() => new Set(favoriteIds), [favoriteIds]);
  const favoriteEntries = useMemo(
    () =>
      favoriteIds.flatMap((id) => {
        const entry = entriesById.get(id);
        return entry ? [entry] : [];
      }),
    [favoriteIds],
  );
  const visibleGroups = useMemo(
    () => filterLaunchpadGroups(launchpadGroups, query),
    [query],
  );
  const visibleEntryCount = useMemo(
    () =>
      visibleGroups.reduce((total, group) => total + group.entries.length, 0),
    [visibleGroups],
  );

  const toggleFavorite = async (entryId: string) => {
    const requestOwnerId = user?.id;
    if (
      !requestOwnerId ||
      !favoritesBelongToCurrentUser ||
      !favoritesReady ||
      favoritesSaving
    )
      return;

    const next = favoriteIds.includes(entryId)
      ? favoriteIds.filter((id) => id !== entryId)
      : [...favoriteIds, entryId];
    setFavoritesSaving(true);
    try {
      const profile = await authApi.updateMetadata({
        [LAUNCHPAD_FAVORITES_METADATA_KEY]: next,
      });
      if (currentUserIdRef.current !== requestOwnerId) return;
      setFavoriteIds(
        parseLaunchpadFavoriteIds(
          profile.metadata?.[LAUNCHPAD_FAVORITES_METADATA_KEY],
          entryIds,
        ),
      );
    } catch {
      if (currentUserIdRef.current === requestOwnerId) {
        toast.error(t("launchpad.favoriteSaveFailed"));
      }
    } finally {
      if (currentUserIdRef.current === requestOwnerId) {
        setFavoritesSaving(false);
      }
    }
  };

  const favoritesDisabled =
    !favoritesBelongToCurrentUser || !favoritesReady || favoritesSaving;
  const metrics = [
    {
      icon: Star,
      label: t("launchpad.myFavorites"),
      value:
        favoritesBelongToCurrentUser && favoritesReady ? favoriteIds.length : "—",
    },
    {
      icon: Grid2X2,
      label: t("launchpad.resourceCategories"),
      value: launchpadGroups.length,
    },
    { icon: Link2, label: t("launchpad.allWebsites"), value: allEntries.length },
  ];

  return (
    <div
      data-company-navigation-shell
      data-launchpad-dashboard
      {...buildFrontendGovernanceSmokeAttributes("ready")}
      className={workbenchSurface.page}
    >
      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-4 overflow-y-auto scroll-smooth p-3 motion-reduce:scroll-auto sm:p-5 lg:p-6">
        <section className="relative overflow-hidden rounded-2xl border border-[#b9ded9] bg-[#edf8f6] px-4 py-5 dark:border-[#235c57] dark:bg-[#102e2b] sm:px-6 sm:py-6">
          <div
            aria-hidden="true"
            className="absolute -bottom-36 left-[28%] h-64 w-[62%] rounded-[50%] border border-[#66c8be]/20"
          />
          <div
            aria-hidden="true"
            className="absolute -bottom-28 left-[38%] h-52 w-[54%] rounded-[50%] border border-[#66c8be]/20"
          />
          <div
            aria-hidden="true"
            className="absolute -bottom-20 left-[48%] h-40 w-[44%] rounded-[50%] border border-[#66c8be]/20"
          />

          <div className="relative grid items-center gap-5 lg:grid-cols-[minmax(0,1fr)_auto]">
            <div className="min-w-0">
              <h1 className="text-xl font-semibold tracking-tight text-[#163b3a] dark:text-[#e8fbf8] sm:text-2xl">
                {t("launchpad.welcome", {
                  name: user?.username || t("launchpad.userFallback"),
                })}{" "}
                <span aria-hidden="true">👋</span>
              </h1>
              <p className="mt-1.5 text-sm text-[#496968] dark:text-[#a8c7c3]">
                {t("launchpad.welcomeSubtitle")}
              </p>

              <div className="relative mt-4 max-w-xl">
                <label htmlFor="launchpad-search" className="sr-only">
                  {t("launchpad.searchLabel")}
                </label>
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-[var(--theme-text-secondary)]"
                />
                <input
                  id="launchpad-search"
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t("launchpad.searchPlaceholder")}
                  className="h-11 w-full rounded-xl border border-[#a9ceca] bg-[var(--theme-bg-card)] pl-10 pr-11 text-sm text-[var(--theme-text)] shadow-sm outline-none transition placeholder:text-[var(--theme-text-tertiary)] focus:border-[#0f8a83] focus:ring-2 focus:ring-[#0f8a83]/20 dark:border-[#3a6864]"
                />
                {query ? (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    aria-label={t("launchpad.clearSearch")}
                    className="absolute right-2 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]"
                  >
                    <X aria-hidden="true" className="size-4" />
                  </button>
                ) : null}
              </div>
            </div>

            <div className="grid grid-cols-3 divide-x divide-[var(--theme-border)] rounded-xl border border-white/80 bg-[var(--theme-bg-card)] px-1 py-3 shadow-sm dark:border-white/10 lg:min-w-[390px]">
              {metrics.map(({ icon: MetricIcon, label, value }) => (
                <div key={label} className="px-2 text-center sm:px-5">
                  <span className="inline-flex items-center gap-1.5 text-[10px] text-[var(--theme-text-secondary)] sm:text-xs">
                    <MetricIcon aria-hidden="true" className="size-3.5" />
                    <span className="truncate">{label}</span>
                  </span>
                  <strong className="mt-1 block text-lg font-semibold tabular-nums text-[var(--theme-text)]">
                    {value}
                  </strong>
                </div>
              ))}
            </div>
          </div>
        </section>

        <div className="flex items-center justify-between gap-3 px-1" aria-live="polite">
          <p className="text-xs text-[var(--theme-text-secondary)]">
            {query
              ? t("launchpad.searchSummary", { count: visibleEntryCount })
              : t("launchpad.catalogSummary", { count: allEntries.length })}
          </p>
          {favoritesBelongToCurrentUser && favoriteEntries.length > 0 && !query ? (
            <a
              href="#launchpad-favorites"
              className="shrink-0 text-xs font-medium text-[#0f8a83] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-ring)]"
            >
              {t("launchpad.jumpToFavorites")}
            </a>
          ) : null}
        </div>

        {!query && favoritesBelongToCurrentUser && favoriteEntries.length > 0 ? (
          <DirectorySection
            id="launchpad-favorites"
            title={t("launchpad.myFavorites")}
            entries={favoriteEntries}
            icon={Star}
            tone="#d28a17"
            favoriteIds={favoriteIdSet}
            favoritesDisabled={favoritesDisabled}
            onToggleFavorite={toggleFavorite}
          />
        ) : null}

        {visibleGroups.length > 0 ? (
          visibleGroups.map((group) => {
            const sourceIndex = launchpadGroups.findIndex(
              (sourceGroup) => sourceGroup.name === group.name,
            );
            const SectionIcon = categoryIcons[group.name] ?? Globe2;
            const title =
              group.name === "内网登录"
                ? t("launchpad.commonServices")
                : group.name === "AI"
                  ? t("launchpad.aiAssistants")
                  : group.name;

            return (
              <DirectorySection
                key={group.id}
                id={group.id}
                title={title}
                entries={group.entries}
                icon={SectionIcon}
                tone={categoryTones[Math.max(sourceIndex, 0) % categoryTones.length]}
                favoriteIds={favoriteIdSet}
                favoritesDisabled={favoritesDisabled}
                onToggleFavorite={toggleFavorite}
              />
            );
          })
        ) : (
          <div className="flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-6 text-center">
            <SearchX
              aria-hidden="true"
              className="size-8 text-[var(--theme-text-secondary)]"
            />
            <h2 className="mt-4 text-base font-semibold text-[var(--theme-text)]">
              {t("launchpad.noResults")}
            </h2>
            <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">
              {t("launchpad.noResultsHint")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
