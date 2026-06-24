/**
 * Session context menu component for session actions
 */

import { useRef, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Edit2,
  Trash2,
  FolderHeart,
  Tag,
  X,
  ChevronLeft,
  Share2,
  Star,
  Check,
} from "lucide-react";
import type { BackendSession } from "../../services/api/session";
import type { Project } from "../../types";
import { DynamicIcon } from "../common/DynamicIcon";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";
import { isSidebarProject } from "../panels/SidebarParts/projectFilters";

interface SessionMenuProps {
  session: BackendSession;
  projects: Project[];
  isOpen: boolean;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  onMoveToProject: (projectId: string | null) => void;
  onShare?: () => void;
  onToggleFavorite?: () => void;
  anchorEl: HTMLElement | null;
  isFavorite?: boolean;
  currentProjectId?: string | null;
}

export function SessionMenu({
  session: _session,
  projects,
  isOpen,
  onClose,
  onRename,
  onDelete,
  onMoveToProject,
  onShare,
  onToggleFavorite,
  anchorEl,
  isFavorite = false,
  currentProjectId,
}: SessionMenuProps) {
  const { t } = useTranslation();
  const menuRef = useRef<HTMLDivElement>(null);
  const [subPanel, setSubPanel] = useState<string | null>(null);

  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 640;
  });

  const swipeRef = useSwipeToClose({
    onClose,
    enabled: isOpen && isMobile,
  });

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // Close menu when clicking outside
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(event.target as Node) &&
        !anchorEl?.contains(event.target as Node)
      ) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen, onClose, anchorEl]);

  // Close on escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (subPanel) setSubPanel(null);
        else onClose();
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isOpen, onClose, subPanel]);

  // Reset sub-panel when menu closes
  useEffect(() => {
    if (!isOpen) setSubPanel(null);
  }, [isOpen]);

  if (!isOpen || !anchorEl) return null;

  const customProjects = projects.filter(isSidebarProject);

  const handleSelectProject = (projectId: string | null) => {
    onMoveToProject(projectId);
    onClose();
  };

  // ── Main menu items ──────────────────────────────────────────────
  const mainMenu = (
    <>
      {/* Rename */}
      <button
        onClick={() => {
          onRename();
          onClose();
        }}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-sm text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)] transition-colors"
      >
        <Edit2 size={16} className="shrink-0" />
        <span>{t("sidebar.rename")}</span>
      </button>

      {/* Move to project — navigates to sub-panel */}
      <button
        onClick={() => setSubPanel("project")}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-sm text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)] transition-colors"
      >
        <FolderHeart size={16} className="shrink-0" />
        <span>{t("sidebar.moveToProject")}</span>
      </button>

      {/* Favorite */}
      {onToggleFavorite && (
        <button
          onClick={() => {
            onToggleFavorite();
            onClose();
          }}
          className={`flex w-full items-center gap-3 px-3 py-2.5 text-sm transition-colors ${
            isFavorite
              ? "text-amber-500 hover:bg-amber-500/10"
              : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)]"
          }`}
        >
          <Star
            size={16}
            className={`shrink-0 ${isFavorite ? "fill-amber-500" : ""}`}
          />
          <span>
            {isFavorite
              ? t("sidebar.removeFromFavorites")
              : t("sidebar.addToFavorites")}
          </span>
        </button>
      )}

      {/* Share */}
      {onShare && (
        <button
          onClick={() => {
            onShare();
            onClose();
          }}
          className="flex w-full items-center gap-3 px-3 py-2.5 text-sm text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)] transition-colors"
        >
          <Share2 size={16} className="shrink-0" />
          <span>{t("sidebar.share")}</span>
        </button>
      )}

      {/* Divider */}
      <div
        className="h-px my-1 mx-2"
        style={{ background: "var(--theme-border)" }}
      />

      {/* Delete */}
      <button
        onClick={() => {
          onDelete();
          onClose();
        }}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-sm text-red-500 hover:bg-red-500/10 transition-colors"
      >
        <Trash2 size={16} className="shrink-0" />
        <span>{t("common.delete")}</span>
      </button>
    </>
  );

  // ── Project sub-panel items ──────────────────────────────────────
  const projectSubPanel = (
    <>
      {/* Back header */}
      <button
        onClick={() => setSubPanel(null)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-sm text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)] transition-colors"
      >
        <ChevronLeft size={16} className="shrink-0" />
        <span>{t("sidebar.moveToProject")}</span>
      </button>

      <div
        className="h-px mx-2"
        style={{ background: "var(--theme-border)" }}
      />

      <div className="py-1">
        {/* Custom projects */}
        {customProjects.map((project) => {
          const isCurrent = currentProjectId === project.id;
          return (
            <button
              key={project.id}
              onClick={() => handleSelectProject(project.id)}
              className={`flex w-full items-center gap-3 px-3 py-2.5 text-sm transition-colors ${
                isCurrent
                  ? "text-[var(--theme-primary)] bg-[var(--theme-primary-light)]"
                  : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)]"
              }`}
            >
              <DynamicIcon
                name={project.icon}
                size={16}
                className={`shrink-0 ${
                  isCurrent
                    ? "text-[var(--theme-primary)]"
                    : "text-[var(--theme-text-secondary)]"
                }`}
              />
              <span className="truncate flex-1 text-left">{project.name}</span>
              {isCurrent && <Check size={14} className="shrink-0" />}
            </button>
          );
        })}

        {/* Uncategorized */}
        <button
          onClick={() => handleSelectProject(null)}
          className={`flex w-full items-center gap-3 px-3 py-2.5 text-sm transition-colors ${
            currentProjectId === null
              ? "text-[var(--theme-primary)] bg-[var(--theme-primary-light)]"
              : "text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)]"
          }`}
        >
          <Tag
            size={16}
            className={`shrink-0 ${
              currentProjectId === null ? "text-[var(--theme-primary)]" : ""
            }`}
          />
          <span className="truncate flex-1 text-left">
            {t("sidebar.uncategorized")}
          </span>
          {currentProjectId === null && (
            <Check size={14} className="shrink-0" />
          )}
        </button>
      </div>
    </>
  );

  // ── Mobile: bottom sheet ──────────────────────────────────────────
  if (isMobile) {
    return (
      <>
        <div
          className="fixed inset-0 z-40 bg-[var(--theme-overlay-strong)] sm:hidden"
          onClick={onClose}
        />
        <div
          ref={(el) => {
            menuRef.current = el;
            swipeRef.current = el;
          }}
          className="fixed bottom-0 left-0 right-0 z-50 sm:hidden rounded-t-lg shadow-[0_8px_24px_rgba(18,38,63,0.12)] max-h-[70vh] overflow-y-auto animate-in fade-in slide-in-from-bottom-4 duration-200"
          style={{ backgroundColor: "var(--theme-bg-card)" }}
        >
          <div className="flex justify-center py-2">
            <div
              className="w-10 h-1 rounded-full"
              style={{ background: "var(--theme-border)" }}
            />
          </div>

          <div
            className="flex items-center justify-between px-4 pb-2"
            style={{ color: "var(--theme-text)" }}
          >
            <span className="text-sm font-medium">
              {t("sidebar.sessionOptions")}
            </span>
            <button
              onClick={onClose}
              className="p-1 rounded-full transition-colors"
              style={{ color: "var(--theme-text-secondary)" }}
            >
              <X size={18} />
            </button>
          </div>

          <div className="px-2 pb-4">
            {subPanel ? projectSubPanel : mainMenu}
          </div>
        </div>
      </>
    );
  }

  // ── Desktop: dropdown ─────────────────────────────────────────────
  const rect = anchorEl.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  const spaceAbove = rect.top;
  const openBelow = spaceBelow >= spaceAbove;

  const menuStyle: React.CSSProperties = {
    position: "fixed",
    ...(openBelow
      ? { top: rect.bottom + 4 }
      : { bottom: window.innerHeight - rect.top + 4 }),
    right: window.innerWidth - rect.right,
    maxHeight: (openBelow ? spaceBelow : spaceAbove) - 16,
    overflowY: "auto",
    zIndex: 50,
  };

  return (
    <div
      ref={menuRef}
      style={{
        ...menuStyle,
        backgroundColor: "var(--theme-bg-card)",
        borderColor: "var(--theme-border)",
      }}
      className="w-56 rounded-lg border shadow-[0_8px_24px_rgba(18,38,63,0.12)] overflow-hidden animate-in fade-in zoom-in-95 duration-150 origin-top-right"
    >
      {subPanel ? projectSubPanel : mainMenu}
    </div>
  );
}
