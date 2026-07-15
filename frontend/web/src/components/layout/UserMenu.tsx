import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { LogOut } from "lucide-react";
import { useAuth } from "../../hooks/useAuth";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

/** Compact account menu for the authenticated workbench shell. */
export function UserMenu() {
  const { t } = useTranslation();
  const { logout, user } = useAuth();
  const [showMenu, setShowMenu] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, right: 0 });
  const [imgError, setImgError] = useState(false);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 640,
  );
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const swipeRef = useSwipeToClose({
    onClose: () => setShowMenu(false),
    enabled: showMenu && isMobile,
  });
  const displayName = user?.username || t("users.user");

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const updateMenuPosition = useCallback(() => {
    if (buttonRef.current && !isMobile) {
      const rect = buttonRef.current.getBoundingClientRect();
      setMenuPosition({
        top: rect.bottom + 8,
        right: window.innerWidth - rect.right,
      });
    }
  }, [isMobile]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        menuRef.current &&
        !menuRef.current.contains(target) &&
        buttonRef.current &&
        !buttonRef.current.contains(target)
      ) {
        setShowMenu(false);
      }
    };

    if (!showMenu) return;

    updateMenuPosition();
    const timer = setTimeout(() => {
      document.addEventListener("click", handleClickOutside);
    }, 0);
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("click", handleClickOutside);
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [showMenu, updateMenuPosition]);

  useEffect(() => {
    if (!showMenu || !isMobile) return;

    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [showMenu, isMobile]);

  const menuItemClass =
    "flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-primary-light)] active:scale-[0.98]";

  const renderAvatar = (sizeClass: string) =>
    user?.avatar_url && !imgError ? (
      <img
        src={user.avatar_url}
        alt={displayName}
        className={`${sizeClass} rounded-full object-cover`}
        onError={() => setImgError(true)}
      />
    ) : (
      <div
        className={`flex ${sizeClass} items-center justify-center rounded-full bg-teal-700`}
      >
        <span className="text-xs font-semibold text-white">
          {displayName.charAt(0).toUpperCase()}
        </span>
      </div>
    );

  const renderMenuContent = () => (
    <>
      <div
        data-user-menu-identity
        className="flex items-center gap-3 px-3 py-3"
      >
        {renderAvatar("size-8")}
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-[var(--theme-text)]">
            {displayName}
          </p>
          {user?.email ? (
            <p className="truncate text-xs text-[var(--theme-text-secondary)]">
              {user.email}
            </p>
          ) : null}
        </div>
      </div>
      <div className="border-t border-[var(--theme-border)] py-1">
        <button
          type="button"
          onClick={() => {
            logout();
            setShowMenu(false);
          }}
          className={`${menuItemClass} text-red-500/70 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-500/10`}
        >
          <LogOut size={16} strokeWidth={1.8} />
          <span>{t("auth.logout")}</span>
        </button>
      </div>
    </>
  );

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        data-user-menu-trigger
        aria-label={displayName}
        onClick={() => setShowMenu((open) => !open)}
        className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-lg transition-all hover:ring-2 hover:ring-[var(--theme-primary-light)] active:scale-95"
      >
        {renderAvatar("size-5")}
      </button>

      {showMenu &&
        createPortal(
          isMobile ? (
            <div
              className="fixed inset-0 z-[100] sm:hidden"
              onClick={() => setShowMenu(false)}
            >
              <div className="fixed inset-0 animate-fade-in bg-slate-950/35" />
              <div
                ref={(element) => {
                  menuRef.current = element;
                  swipeRef.current = element;
                }}
                className="fixed inset-x-0 bottom-0 z-[101] max-h-[85vh] overflow-y-auto rounded-t-lg shadow-[0_8px_24px_rgba(18,38,63,0.12)] animate-slide-up-sheet"
                style={{ backgroundColor: "var(--theme-bg-card)" }}
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex justify-center pb-1 pt-3">
                  <div className="h-1 w-9 rounded-full bg-[var(--theme-text-secondary)] opacity-25" />
                </div>
                {renderMenuContent()}
                <div className="h-[env(safe-area-inset-bottom)]" />
              </div>
            </div>
          ) : (
            <>
              <div
                className="fixed inset-0 z-[300]"
                onClick={() => setShowMenu(false)}
              />
              <div
                ref={menuRef}
                className="fixed z-[301] w-60 overflow-y-auto rounded-lg border shadow-[0_8px_18px_rgba(18,38,63,0.08)] animate-scale-in"
                style={{
                  top: `${menuPosition.top}px`,
                  right: `${menuPosition.right}px`,
                  backgroundColor: "var(--theme-bg-card)",
                  borderColor: "var(--theme-border)",
                }}
                onClick={(event) => event.stopPropagation()}
              >
                {renderMenuContent()}
              </div>
            </>
          ),
          document.body,
        )}
    </div>
  );
}
