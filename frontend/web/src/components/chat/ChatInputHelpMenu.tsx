import { Button, DropdownMenu } from "@lobehub/ui/base-ui";
import { useState } from "react";
import { useAppElement } from "@lobehub/ui/es/ThemeProvider/index";
import { createPortal } from "react-dom";
import { CircleHelp, Keyboard } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ShortcutDialog } from "./ChatInputShortcuts";
import { GITHUB_URL } from "../../constants";

export function ChatInputHelpMenu() {
  const { t } = useTranslation();
  const appElement = useAppElement();
  const [shortcutDialogOpen, setShortcutDialogOpen] = useState(false);
  return createPortal(
    <div className="fixed bottom-2 right-2 hidden sm:block">
      <DropdownMenu placement="topRight" items={[
        { key: "docs", icon: CircleHelp, label: t("chat.helpDocs", "帮助文档"), onClick: () => window.open(GITHUB_URL, "_blank", "noopener,noreferrer") },
        { key: "shortcuts", icon: Keyboard, label: t("chat.keyboardShortcuts", "键盘快捷键"), onClick: () => setShortcutDialogOpen(true) },
      ]}>
        <Button type="text" shape="circle" aria-label={t("chat.helpDocs")} icon={<CircleHelp size={16} />} />
      </DropdownMenu>
      <ShortcutDialog open={shortcutDialogOpen} onClose={() => setShortcutDialogOpen(false)} />
    </div>,
    appElement ?? document.body,
  );
}
