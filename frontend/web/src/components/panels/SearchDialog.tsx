import { Button, Input, Spin, ModalRoot, ModalPortal, ModalBackdrop, ModalPopup, ModalTitle } from "@lobehub/ui/base-ui";
/**
 * Search dialog for finding sessions across all projects.
 *
 * Features: fade+scale entrance, keyboard navigation
 * (↑/↓/Enter/Escape), infinite scroll, smooth scrolling.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useInView } from "react-intersection-observer";
import { useTranslation } from "react-i18next";
import { Search, X, Hash } from "lucide-react";
import { sessionApi, type BackendSession } from "../../services/api";
import { getSessionTitle } from "./sessionHelpers";
import { SkeletonList } from "../skeletons";

const PAGE_SIZE = 30;

interface SearchResult {
  session: BackendSession;
  projectName: string | null;
}

interface SearchDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSelectSession: (sessionId: string) => void;
}

export function SearchDialog({
  isOpen,
  onClose,
  onSelectSession,
}: SearchDialogProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Map<number, HTMLElement>>(new Map());
  const [activeIndex, setActiveIndex] = useState(-1);

  // ── Session state (independent pagination) ────────────────────
  const [allSessions, setAllSessions] = useState<SearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestRef = useRef<((reset: boolean) => void) | null>(null);
  const wasOpenRef = useRef(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [skeletonVisible, setSkeletonVisible] = useState(false);

  // Infinite scroll sentinel
  const { ref: sentinelRef, inView } = useInView({
    threshold: 0,
    rootMargin: "200px",
  });

  // ── Reset active index when results change ─────────────────────
  useEffect(() => {
    setActiveIndex(-1);
    itemRefs.current.clear();
  }, [allSessions]);

  // One owner per open/query generation owns both reset and pagination.
  // Cleanup invalidates receipts (including finally/RAF), independently of abort.
  useEffect(() => {
    const opening = isOpen && !wasOpenRef.current;
    wasOpenRef.current = isOpen;
    if (!isOpen) return;

    const owner = { current: true, busy: false, skip: 0, hasMore: false };
    const q = searchQuery.trim();
    setAllSessions([]);
    setHasMore(false);
    setFailed(false);
    setIsLoading(true);
    setIsLoadingMore(false);
    setSkeletonVisible(true);

    async function fetchSessions(reset: boolean) {
      if (!owner.current || owner.busy || (!reset && !owner.hasMore)) return;
      owner.busy = true;
      const targetSkip = reset ? 0 : owner.skip;
      setFailed(false);
      if (reset) {
        setIsLoading(true);
        setSkeletonVisible(true);
      } else {
        setIsLoadingMore(true);
      }
      try {
        const response = await sessionApi.list({
          limit: PAGE_SIZE,
          skip: targetSkip,
          status: "active",
          ...(q ? { search: q } : {}),
        });
        if (!owner.current) return;
        const newSessions =
          "sessions" in response
            ? response.sessions
            : Array.isArray(response)
              ? response
              : [];
        const newHasMore = "has_more" in response ? response.has_more : false;
        const results: SearchResult[] = newSessions.map((s) => ({
          session: s,
          projectName:
            ((s.metadata as Record<string, unknown>)?.project_name as
              | string
              | null) ?? null,
        }));
        setAllSessions((previous) => reset ? results : [...previous, ...results]);
        owner.skip = targetSkip + results.length;
        owner.hasMore = newSessions.length > 0 ? newHasMore : false;
        setHasMore(owner.hasMore);
      } catch {
        if (owner.current) setFailed(true);
      } finally {
        if (owner.current) {
          owner.busy = false;
          setIsLoading(false);
          setIsLoadingMore(false);
          if (reset) {
            requestAnimationFrame(() => {
              if (owner.current) setSkeletonVisible(false);
            });
          }
        }
      }
    }

    requestRef.current = fetchSessions;
    // Opening already performs the initial load; only input edits debounce.
    const timer = opening ? null : setTimeout(() => fetchSessions(true), 200);
    if (opening) void fetchSessions(true);
    return () => {
      owner.current = false;
      requestRef.current = null;
      if (timer !== null) clearTimeout(timer);
    };
  }, [isOpen, searchQuery]);

  // Failed pages wait for an explicit retry instead of a visible sentinel loop.
  useEffect(() => {
    if (isOpen && inView && hasMore && !failed && !isLoadingMore && !isLoading) {
      requestRef.current?.(false);
    }
  }, [isOpen, inView, hasMore, failed, isLoadingMore, isLoading]);

  // ── Keyboard navigation ────────────────────────────────────────
  // Keep navigation inside the popup: Base UI stops composite keys bubbling.
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((prev) => {
        const next = prev < allSessions.length - 1 ? prev + 1 : 0;
        itemRefs.current.get(next)?.scrollIntoView({ block: "nearest" });
        return next;
      });
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((prev) => {
        if (prev <= 0) {
          inputRef.current?.focus();
          return -1;
        }
        const next = prev - 1;
        itemRefs.current.get(next)?.scrollIntoView({ block: "nearest" });
        return next;
      });
      return;
    }
    if (e.key === "Enter") {
      if (activeIndex >= 0 && activeIndex < allSessions.length) {
        e.preventDefault();
        onSelectSession(allSessions[activeIndex].session.id);
      }
      return;
    }
  };

  const hasQuery = searchQuery.trim().length > 0;

  return (
    <ModalRoot open={isOpen} onOpenChange={(open) => { if (!open) onClose(); }}>
      <ModalPortal>
        <ModalBackdrop />
        <ModalPopup width="min(32rem, calc(100vw - 2rem))" className="workbench-modal" initialFocus={inputRef} onKeyDown={handleKeyDown}>
          <ModalTitle className="sr-only">{t("sidebar.searchSessions")}</ModalTitle>
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3.5">
          <Input
            ref={inputRef}
            prefix={<Search size={16} />}
            aria-label={t("sidebar.searchSessions")}
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("sidebar.searchSessions") + "..."}
            className="flex-1 min-w-0 text-[15px] bg-transparent text-stone-800 dark:text-stone-100 placeholder:text-stone-400 dark:placeholder:text-stone-500 focus:outline-none"
          />
          {searchQuery && (
            <Button type="text"
              onClick={() => setSearchQuery("")}
              aria-label={t("common.clear", "清空")}
              className="flex-shrink-0 flex items-center justify-center w-5 h-5 rounded-full text-[var(--theme-text-tertiary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)] transition-all"
            >
              <X size={12} strokeWidth={2.5} />
            </Button>
          )}
          <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium text-[var(--theme-text-tertiary)] bg-[var(--theme-bg-sidebar)] rounded-md border border-[var(--theme-border)]">
            ESC
          </kbd>
        </div>

        {/* Divider */}
        <div className="mx-4 h-px bg-[var(--theme-border)]" />

        {/* Results list */}
        <div
          className="h-[50vh] overflow-y-auto scroll-smooth py-2"
          style={{
            scrollbarWidth: "thin",
            scrollbarColor: "transparent transparent",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLDivElement).style.scrollbarColor =
              "rgba(168,162,158,0.3) transparent";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLDivElement).style.scrollbarColor =
              "transparent transparent";
          }}
        >
          {/* Results area — grid stacking prevents height jump during crossfade */}
          <div className="grid h-full">
            {/* Loading state — fades out when results arrive */}
            <div
              className={`[grid-area:1/1] h-full transition-opacity duration-150 ease-out ${
                skeletonVisible
                  ? "opacity-100"
                  : "opacity-0 pointer-events-none"
              }`}
            >
              <SkeletonList
                count={12}
                className="h-full flex flex-col py-2"
                compact
              />
            </div>

            {/* Session items — fades in when skeleton fades out */}
            <div
              className={`[grid-area:1/1] pb-4 transition-opacity duration-150 ease-out ${
                !isLoading ? "opacity-100" : "opacity-0 pointer-events-none"
              }`}
            >
              {/* Empty search results */}
              {!failed && hasQuery && allSessions.length === 0 && (
                <div className="px-4 py-10 text-center">
                  <p className="text-sm text-stone-400 dark:text-stone-500">
                    {t("sidebar.noSearchResults")}
                  </p>
                  <p className="mt-1 text-xs text-stone-300 dark:text-stone-600">
                    &quot;{searchQuery}&quot;
                  </p>
                </div>
              )}

              {/* Session items */}
              {allSessions.map(({ session, projectName }, index) => {
                const isActive = index === activeIndex;
                const searchMatch =
                  typeof (session.metadata as Record<string, unknown>)
                    ?.search_match === "string"
                    ? ((session.metadata as Record<string, unknown>)
                        .search_match as string)
                    : null;
                return (
                  <Button type="text"
                    key={session.id}
                    ref={(el) => {
                      if (el) {
                        itemRefs.current.set(index, el);
                      } else {
                        itemRefs.current.delete(index);
                      }
                    }}
                    onClick={() => onSelectSession(session.id)}
                    onMouseEnter={() => setActiveIndex(index)}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-all duration-75 group ${
                      isActive
                        ? "bg-[var(--theme-primary-light)]"
                        : "hover:bg-[var(--theme-bg-sidebar)]"
                    }`}
                  >
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-stone-700 dark:text-stone-200 truncate leading-snug">
                        {getSessionTitle(session, t)}
                      </span>
                      {searchMatch && (
                        <span
                          title={searchMatch}
                          className="mt-0.5 block text-xs text-stone-400 dark:text-stone-500 truncate leading-relaxed"
                        >
                          {searchMatch}
                        </span>
                      )}
                    </span>
                    {projectName && (
                      <span className="flex-shrink-0 flex items-center gap-1 text-[11px] text-[var(--theme-text-tertiary)] bg-[var(--theme-bg-sidebar)] border border-[var(--theme-border)] px-1.5 py-0.5 rounded-md">
                        <Hash size={9} strokeWidth={2} />
                        {projectName}
                      </span>
                    )}
                  </Button>
                );
              })}

              {failed && (
                <Button type="text" onClick={() => requestRef.current?.(allSessions.length === 0)}>
                  {t("common.retry", "Retry")}
                </Button>
              )}

              {/* Infinite scroll sentinel */}
              {hasMore && (
                <div ref={sentinelRef} className="flex justify-center py-3">
                  {isLoadingMore && (
                    <Spin size={16} />
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Bottom hint bar */}
        {!isLoading && hasQuery && allSessions.length > 0 && (
          <>
            <div className="mx-4 h-px bg-[var(--theme-border)]" />
            <div className="flex items-center justify-between px-4 py-2 text-[11px] text-stone-400 dark:text-stone-500">
              <span>
                {allSessions.length} {hasMore ? "..." : ""}
              </span>
              <div className="flex items-center gap-2">
                <span className="flex items-center gap-0.5">
                  <kbd className="px-1 py-0.5 rounded bg-[var(--theme-bg-sidebar)] border border-[var(--theme-border)] text-[10px]">
                    ↑
                  </kbd>
                  <kbd className="px-1 py-0.5 rounded bg-[var(--theme-bg-sidebar)] border border-[var(--theme-border)] text-[10px]">
                    ↓
                  </kbd>
                  <span className="ml-0.5">{t("sidebar.navigate")}</span>
                </span>
                <span className="flex items-center gap-0.5">
                  <kbd className="px-1.5 py-0.5 rounded bg-[var(--theme-bg-sidebar)] border border-[var(--theme-border)] text-[10px]">
                    ↵
                  </kbd>
                  <span className="ml-0.5">{t("sidebar.open")}</span>
                </span>
              </div>
            </div>
          </>
        )}
        </ModalPopup>
      </ModalPortal>
    </ModalRoot>
  );
}
