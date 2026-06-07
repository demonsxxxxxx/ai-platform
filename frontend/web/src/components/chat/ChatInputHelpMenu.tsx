import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { CircleHelp, ExternalLink, Keyboard } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ShortcutDialog } from "./ChatInputShortcuts";

export function ChatInputHelpMenu() {
  const { t } = useTranslation();
  const [helpMenuOpen, setHelpMenuOpen] = useState(false);
  const [shortcutDialogOpen, setShortcutDialogOpen] = useState(false);
  const helpMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!helpMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        helpMenuRef.current &&
        !helpMenuRef.current.contains(e.target as Node)
      ) {
        setHelpMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [helpMenuOpen]);

  return createPortal(
    <div
      ref={helpMenuRef}
      className="hidden sm:block fixed bottom-2 right-2 z-50"
    >
      <button
        type="button"
        aria-label="Help"
        aria-expanded={helpMenuOpen}
        onClick={() => setHelpMenuOpen((v) => !v)}
        className="flex items-center justify-center w-8 h-8 text-sm font-medium rounded-full shadow-md backdrop-blur-sm transition-all duration-200 hover:shadow-lg hover:scale-110 active:scale-95"
        style={{
          backgroundColor:
            "color-mix(in srgb, var(--theme-bg-card) 85%, transparent)",
          border: "1px solid var(--theme-border)",
          color: "var(--theme-text-secondary)",
        }}
      >
        <CircleHelp size={16} />
      </button>
      {helpMenuOpen && (
        <div
          role="menu"
          className="absolute bottom-full right-0 mb-2 w-[200px] rounded-xl p-1 shadow-lg"
          style={{
            backgroundColor: "var(--theme-bg-card)",
            border: "1px solid var(--theme-border)",
          }}
        >
          <a
            href="https://yanyutin753.github.io/LambChat/"
            target="_blank"
            rel="noopener noreferrer"
            role="menuitem"
            onClick={() => setHelpMenuOpen(false)}
            className="flex gap-2.5 items-center w-full px-3 py-2 text-[13px] rounded-lg cursor-pointer transition-colors no-underline"
            style={{ color: "var(--theme-text)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor =
                "var(--theme-bg-hover, rgba(128,128,128,0.08))";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = "transparent";
            }}
          >
            <CircleHelp
              size={16}
              className="shrink-0"
              style={{ color: "var(--theme-text-secondary)" }}
            />
            <span className="flex-1">{t("chat.helpDocs", "帮助文档")}</span>
            <ExternalLink
              size={12}
              style={{ color: "var(--theme-text-secondary)", opacity: 0.5 }}
            />
          </a>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setHelpMenuOpen(false);
              setShortcutDialogOpen(true);
            }}
            className="flex gap-2.5 items-center w-full px-3 py-2 text-[13px] rounded-lg cursor-pointer transition-colors"
            style={{ color: "var(--theme-text)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor =
                "var(--theme-bg-hover, rgba(128,128,128,0.08))";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = "transparent";
            }}
          >
            <Keyboard
              size={16}
              className="shrink-0"
              style={{ color: "var(--theme-text-secondary)" }}
            />
            <span>{t("chat.keyboardShortcuts", "键盘快捷键")}</span>
          </button>
        </div>
      )}
      <ShortcutDialog
        open={shortcutDialogOpen}
        onClose={() => setShortcutDialogOpen(false)}
      />
    </div>,
    document.body,
  );
}
