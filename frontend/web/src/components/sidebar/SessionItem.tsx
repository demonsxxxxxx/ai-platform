/**
 * Session item component with inline title editing.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { MoreHorizontal } from "lucide-react";
import toast from "react-hot-toast";
import type { BackendSession } from "../../services/api/session";
import { sessionApi } from "../../services/api";
import { SessionMenu } from "./SessionMenu";
import { shouldBlockSessionSelection } from "../../utils/sessionSelectionGuard";

interface SessionItemProps {
  session: BackendSession;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onSessionUpdate: (session: BackendSession) => void;
}

export function SessionItem({
  session,
  isActive,
  onSelect,
  onDelete,
  onSessionUpdate,
}: SessionItemProps) {
  const { t } = useTranslation();
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [isTouched, setIsTouched] = useState(false);
  const touchShowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);

  // Get session title from various sources
  const getSessionTitle = useCallback(
    (s: BackendSession) => {
      if (s.name) return s.name;
      const meta = s.metadata as Record<string, unknown>;
      if (meta?.title) return meta.title as string;
      return t("sidebar.newChat");
    },
    [t],
  );

  // Start editing
  const handleStartEdit = () => {
    setEditTitle(getSessionTitle(session));
    setIsEditing(true);
    setIsMenuOpen(false);
  };

  // Focus input when editing starts
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  // Save title
  const handleSaveTitle = async () => {
    const trimmedTitle = editTitle.trim();

    // Don't save if title hasn't changed or is empty
    if (!trimmedTitle || trimmedTitle === getSessionTitle(session)) {
      setIsEditing(false);
      return;
    }

    setIsSaving(true);
    try {
      const response = await sessionApi.update(session.id, {
        name: trimmedTitle,
      });
      if (response.session) {
        onSessionUpdate(response.session);
        toast.success(t("sidebar.renamed"));
      }
    } catch (error) {
      console.error("Failed to update session title:", error);
      toast.error(t("sidebar.renameFailed"));
    } finally {
      setIsSaving(false);
      setIsEditing(false);
    }
  };

  // Cancel editing
  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditTitle("");
  };

  // Handle key events
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSaveTitle();
    } else if (e.key === "Escape") {
      e.preventDefault();
      handleCancelEdit();
    }
  };

  // Handle menu button click
  const handleMenuClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setMenuAnchor(menuButtonRef.current);
    setIsMenuOpen(true);
  };

  // Touch: show menu button, auto-hide after 3s
  const handleItemTouchStart = () => {
    if (isEditing) return;
    if (touchShowTimerRef.current) clearTimeout(touchShowTimerRef.current);
    setIsTouched(true);
    touchShowTimerRef.current = setTimeout(() => setIsTouched(false), 3000);
  };

  // Cleanup timers
  useEffect(() => {
    return () => {
      if (touchShowTimerRef.current) clearTimeout(touchShowTimerRef.current);
    };
  }, []);

  // Get display title
  const displayTitle = getSessionTitle(session);

  return (
    <>
      <div
        onTouchStart={handleItemTouchStart}
        onClick={() => {
          if (shouldBlockSessionSelection(window.location.pathname)) {
            return;
          }
          if (!isEditing) {
            onSelect();
          }
        }}
        className={`group relative flex cursor-pointer items-center gap-3 h-10 rounded-lg px-[9px] transition-colors ${
          isActive
            ? "bg-[var(--theme-sidebar-panel-muted)]"
            : "hover:bg-[var(--theme-sidebar-panel-muted)]"
        }`}
      >
        {/* Title - editable or display */}
        <div className="min-w-0 flex-1">
          {isEditing ? (
            <input
              ref={inputRef}
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={handleSaveTitle}
              disabled={isSaving}
              className="w-full rounded border border-[var(--theme-border-strong)] bg-transparent px-1.5 py-0.5 text-[13px] text-[var(--theme-text)] focus:outline-none focus:ring-1 focus:ring-[var(--theme-ring)]"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <div
              className={`truncate text-[13px] transition-colors ${
                isActive
                  ? "font-medium text-[var(--theme-text)]"
                  : "text-[var(--theme-text-secondary)] group-hover:text-[var(--theme-text)]"
              }`}
            >
              {displayTitle}
            </div>
          )}
        </div>

        {/* Unread dot - hidden when session is active (user is viewing it) */}
        {!isEditing && !isActive && (session.unread_count ?? 0) > 0 && (
          <span className="inline-flex h-4 min-w-[16px] shrink-0 items-center justify-center rounded-full bg-[var(--theme-danger)] px-1 text-[10px] font-medium leading-none text-[var(--theme-primary-foreground)]">
            {session.unread_count}
          </span>
        )}
        {!isEditing && (
          <button
            ref={menuButtonRef}
            onClick={handleMenuClick}
            className="flex-shrink-0 rounded p-1 text-[var(--theme-text-tertiary)] opacity-0 transition-all hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)] group-hover:opacity-100"
            style={isTouched ? { opacity: 1 } : undefined}
            title={t("sidebar.moreOptions")}
          >
            <MoreHorizontal
              size={14}
              className="text-current"
            />
          </button>
        )}
      </div>

      {/* Context Menu */}
      <SessionMenu
        session={session}
        isOpen={isMenuOpen}
        onClose={() => setIsMenuOpen(false)}
        onRename={handleStartEdit}
        onDelete={onDelete}
        anchorEl={menuAnchor}
      />
    </>
  );
}
