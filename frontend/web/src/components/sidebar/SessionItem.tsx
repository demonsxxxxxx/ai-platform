import { Button, Input } from "@lobehub/ui/base-ui";
/**
 * Session item component with inline title editing.
 */

import { useState, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { MoreHorizontal } from "lucide-react";
import { toast } from "@lobehub/ui/base-ui";
import type { BackendSession } from "../../services/api/session";
import { sessionApi } from "../../services/api";
import { SessionMenu } from "./SessionMenu";
import { ThreadListRow } from "../panels/SidebarParts/ThreadList";
import { toSessionThreadListItem } from "../panels/SidebarParts/sessionThreadListAdapter";

interface SessionItemProps {
  session: BackendSession;
  isActive: boolean;
  onSelect: () => void;
  onDelete: (returnFocus: HTMLElement | null) => void;
  onSessionUpdate: (session: BackendSession) => void;
  onRenameSession?: (
    sessionId: string,
    title: string,
  ) => Promise<BackendSession | void>;
}

export function SessionItem({
  session,
  isActive,
  onSelect,
  onDelete,
  onSessionUpdate,
  onRenameSession,
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

  const updateOwnerRef = useRef<typeof onSessionUpdate | null>(onSessionUpdate);
  useLayoutEffect(() => {
    updateOwnerRef.current = onSessionUpdate;
    return () => { updateOwnerRef.current = null; };
  }, [onSessionUpdate]);

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
      const response = onRenameSession
        ? { session: await onRenameSession(session.id, trimmedTitle) }
        : await sessionApi.update(session.id, {
            name: trimmedTitle,
          });
      if (response.session) {
        // The list owner survives collapsed/unmounted rows and fences account
        // changes itself; only this row's local UI work depends on its mount.
        onSessionUpdate(response.session);
        if (updateOwnerRef.current !== onSessionUpdate) return;
      }
      if (updateOwnerRef.current === onSessionUpdate) {
        toast.success(t("sidebar.renamed"));
      }
    } catch (error) {
      if (updateOwnerRef.current !== onSessionUpdate) return;
      console.error("Failed to update session title:", error);
      toast.error(t("sidebar.renameFailed"));
    } finally {
      if (updateOwnerRef.current === onSessionUpdate) {
        setIsSaving(false);
        setIsEditing(false);
      }
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

  const displayTitle = getSessionTitle(session);
  const thread = toSessionThreadListItem(session, displayTitle);

  return (
    <>
      <ThreadListRow
      thread={thread}
      active={isActive}
      editing={isEditing}
      onSelect={() => {
        if (!isEditing) onSelect();
      }}
      onTouchStart={handleItemTouchStart}
      title={
        isEditing ? (
          <Input
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
          <span
            className={`block truncate transition-colors ${
              isActive
                ? "font-medium text-[var(--theme-text)]"
                : "text-[var(--theme-text-secondary)] group-hover:text-[var(--theme-text)]"
            }`}
          >
            {displayTitle}
          </span>
        )
      }
      actions={
        !isEditing ? (
          <Button
            type="text"
            ref={menuButtonRef}
            onClick={handleMenuClick}
            className="flex-shrink-0 rounded p-1 text-[var(--theme-text-tertiary)] hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)]"
            style={isTouched ? { opacity: 1 } : undefined}
            title={t("sidebar.moreOptions")}
          >
            <MoreHorizontal size={14} className="text-current" />
          </Button>
        ) : undefined
      }
      actionsVisible={isTouched}
      />
      <SessionMenu
        session={session}
        isOpen={isMenuOpen}
        onClose={() => setIsMenuOpen(false)}
        onRename={handleStartEdit}
        onDelete={() => onDelete(menuButtonRef.current)}
        anchorEl={menuAnchor}
      />
    </>
  );
}
