import { Button, Drawer, DropdownMenuRoot, DropdownMenuPortal, DropdownMenuPositioner, DropdownMenuPopup, DropdownMenuItem, DropdownMenuSeparator } from "@lobehub/ui/base-ui";
/**
 * Session context menu component for session actions
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Edit2,
  Trash2,
  X,
} from "lucide-react";
import type { BackendSession } from "../../services/api/session";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

interface SessionMenuProps {
  session: BackendSession;
  isOpen: boolean;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  anchorEl: HTMLElement | null;
}

export function SessionMenu({ isOpen, onClose, onRename, onDelete, anchorEl }: SessionMenuProps) {
  const { t } = useTranslation();
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth < 640);
  const swipeRef = useSwipeToClose({ onClose, enabled: isOpen && isMobile });
  useEffect(() => {
    const resize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const rename = () => { onRename(); onClose(); };
  const remove = () => { onDelete(); onClose(); };

  if (isMobile) return (
    <Drawer open={isOpen} onClose={onClose} placement="bottom" height="auto"
      styles={{ panel: { maxHeight: "70dvh" } }} closable={false}
      title={t("sidebar.sessionOptions")}
      extra={<Button type="text" aria-label={t("common.close")} onClick={onClose}><X size={18} /></Button>}
    >
      <div ref={(element) => { swipeRef.current = element; }} className="flex flex-col gap-1">
        <Button type="text" block onClick={rename}><Edit2 size={16} />{t("sidebar.rename")}</Button>
        <Button type="text" danger block onClick={remove}><Trash2 size={16} />{t("common.delete")}</Button>
      </div>
    </Drawer>
  );
  return (
    <DropdownMenuRoot open={isOpen && !!anchorEl} modal={false} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DropdownMenuPortal>
        <DropdownMenuPositioner anchor={anchorEl} placement="bottomRight">
          <DropdownMenuPopup aria-label={t("sidebar.sessionOptions")} finalFocus={() => anchorEl}>
            <DropdownMenuItem onClick={rename}><Edit2 size={16} />{t("sidebar.rename")}</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem danger onClick={remove}><Trash2 size={16} />{t("common.delete")}</DropdownMenuItem>
          </DropdownMenuPopup>
        </DropdownMenuPositioner>
      </DropdownMenuPortal>
    </DropdownMenuRoot>
  );
}
