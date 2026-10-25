import { Button, Drawer, DropdownMenu } from "@lobehub/ui/base-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { LogOut, X } from "lucide-react";
import { useAuth } from "../../hooks/useAuth";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

/** Compact account menu for the authenticated workbench shell. */
export function UserMenu({ showLabel = false }: { showLabel?: boolean }) {
  const { t } = useTranslation();
  const { logout, user } = useAuth();
  const [showMenu, setShowMenu] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.innerWidth < 640);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const swipeRef = useSwipeToClose({ onClose: () => setShowMenu(false), enabled: showMenu && isMobile });
  const displayName = user?.username || t("users.user");
  useEffect(() => {
    const resize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  const renderAvatar = (sizeClass: string) =>
    user?.avatar_url && !imgError ? (
      <img
        data-user-avatar
        src={user.avatar_url}
        alt={displayName}
        className={`${sizeClass} rounded-full object-cover`}
        onError={() => setImgError(true)}
      />
    ) : (
      <div data-user-avatar
        className={`flex ${sizeClass} items-center justify-center rounded-full bg-neutral-700`}
      >
        <span className="text-xs font-semibold text-[var(--theme-primary-foreground)]">
          {displayName.charAt(0).toUpperCase()}
        </span>
      </div>
    );

  const identity = (
    <div data-user-menu-identity className="flex items-center gap-3 p-3">
      {renderAvatar("size-8")}
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{displayName}</p>
        {user?.email ? <p className="truncate text-xs text-[var(--theme-text-secondary)]">{user.email}</p> : null}
      </div>
    </div>
  );
  const leave = () => { logout(); setShowMenu(false); };
  const trigger = (
    <Button type="text" ref={buttonRef} data-user-menu-trigger aria-label={displayName}
      onClick={isMobile ? () => setShowMenu((open) => !open) : undefined}
      className={showLabel ? "min-w-0 px-1" : undefined} icon={renderAvatar("size-5")}
    >
      {showLabel ? <span className="truncate text-xs">{displayName}</span> : null}
    </Button>
  );
  if (!isMobile) return (
    <DropdownMenu open={showMenu} onOpenChange={setShowMenu} placement={showLabel ? "topLeft" : "rightBottom"}
      header={identity} items={[{ key: "logout", icon: LogOut, label: t("auth.logout"), danger: true, onClick: leave }]}
    >{trigger}</DropdownMenu>
  );
  return (
    <>
      {trigger}
      <Drawer open={showMenu} onClose={() => setShowMenu(false)} placement="bottom" height="auto" closable={false}
        title={t("users.user")} styles={{ panel: { maxHeight: "85dvh" } }}
        extra={<Button type="text" aria-label={t("common.close")} onClick={() => setShowMenu(false)} icon={<X size={18} />} />}
      >
        <div ref={(element) => { swipeRef.current = element; }}>
          {identity}
          <Button type="text" danger block onClick={leave} icon={<LogOut size={16} />}>{t("auth.logout")}</Button>
        </div>
      </Drawer>
    </>
  );
}
