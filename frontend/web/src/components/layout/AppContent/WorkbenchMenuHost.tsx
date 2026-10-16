import { DropdownMenuRoot, DropdownMenuPortal, DropdownMenuPositioner, DropdownMenuPopup, DropdownMenuItem } from "@lobehub/ui/base-ui";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  History,
  ListTree,
  MessageSquarePlus,
  Moon,
  Sun,
} from "lucide-react";

import { useTheme } from "../../../contexts/ThemeContext";
import type { TabType } from "./types";

interface WorkbenchMenuPosition {
  bottom: number;
  left?: number;
  right?: number;
}

interface WorkbenchMenuHostProps {
  activeTab: TabType;
  onNewSession: () => void;
  currentRunId?: string | null;
  onOpenRunPlayback?: () => void;
  onToggleOutline?: () => void;
  showOutlineButton?: boolean;
  allowNewSessionAction?: boolean;
  newSessionActionLabel?: string;
}

/** Portals the shared workbench menu without adding page layout. */
export function WorkbenchMenuHost({
  activeTab,
  onNewSession,
  currentRunId,
  onOpenRunPlayback,
  onToggleOutline,
  showOutlineButton,
  allowNewSessionAction = true,
  newSessionActionLabel,
}: WorkbenchMenuHostProps) {
  const { t } = useTranslation();
  const { theme, toggleTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<WorkbenchMenuPosition>({
    bottom: 8,
    right: 8,
  });

  useEffect(() => {
    const handleOpen = (event: Event) => {
      setMenuPosition(
        (event as CustomEvent<WorkbenchMenuPosition>).detail,
      );
      setMenuOpen(true);
    };
    window.addEventListener("workbench-menu-open", handleOpen);
    return () => window.removeEventListener("workbench-menu-open", handleOpen);
  }, []);

  const anchor = useMemo(() => ({
    getBoundingClientRect: () => new DOMRect(
      menuPosition.left ?? window.innerWidth - (menuPosition.right ?? 8),
      window.innerHeight - menuPosition.bottom, 0, 0,
    ),
  }), [menuPosition]);
  const showRunPlaybackButton = activeTab === "chat" && !!currentRunId && !!onOpenRunPlayback;
  return (
    <DropdownMenuRoot open={menuOpen} modal={false} onOpenChange={setMenuOpen}>
      <DropdownMenuPortal>
        <DropdownMenuPositioner anchor={anchor} placement={menuPosition.left == null ? "topRight" : "topLeft"}>
          <DropdownMenuPopup className="w-56" aria-label={t("common.menu")}>
            {showOutlineButton && onToggleOutline ? <DropdownMenuItem onClick={() => { onToggleOutline(); setMenuOpen(false); }}><ListTree size={16} />{t("chat.outline")}</DropdownMenuItem> : null}
            {showRunPlaybackButton ? <DropdownMenuItem onClick={() => { onOpenRunPlayback?.(); setMenuOpen(false); }}><History size={16} />{t("runPlayback.menuLabel")}</DropdownMenuItem> : null}
            {activeTab === "chat" && allowNewSessionAction ? <DropdownMenuItem onClick={() => { onNewSession(); setMenuOpen(false); }}><MessageSquarePlus size={16} />{newSessionActionLabel ?? t("sidebar.newChat")}</DropdownMenuItem> : null}
            <DropdownMenuItem onClick={() => { toggleTheme(); setMenuOpen(false); }}>
              {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
              {t(theme === "light" ? "theme.switchToDark" : "theme.switchToLight")}
            </DropdownMenuItem>
          </DropdownMenuPopup>
        </DropdownMenuPositioner>
      </DropdownMenuPortal>
    </DropdownMenuRoot>
  );
}
