import { useTranslation } from "react-i18next";
import type { BackendSession } from "../../services/api/session";
import { shouldBlockSessionSelection } from "../../utils/sessionSelectionGuard";

interface SessionItemProps {
  session: BackendSession;
  isActive: boolean;
  onSelect: () => void;
}

export function SessionItem({
  session,
  isActive,
  onSelect,
}: SessionItemProps) {
  const { t } = useTranslation();

  const meta = session.metadata as Record<string, unknown>;
  const displayTitle =
    session.name || (meta?.title as string | undefined) || t("sidebar.newChat");

  return (
    <div
      onClick={() => {
        if (shouldBlockSessionSelection(window.location.pathname)) {
          return;
        }
        onSelect();
      }}
      className={`group relative flex cursor-pointer items-center gap-3 h-10 rounded-[10px] px-[9px] transition-colors ${
        isActive
          ? "bg-stone-100 dark:bg-stone-800/60"
          : "hover:bg-stone-100 dark:hover:bg-stone-800/40"
      }`}
    >
      <div className="min-w-0 flex-1">
        <div
          className={`truncate text-[13px] transition-colors ${
            isActive
              ? "text-stone-800 dark:text-stone-100 font-medium"
              : "text-stone-600 dark:text-stone-300 group-hover:text-stone-700 dark:group-hover:text-stone-200"
          }`}
        >
          {displayTitle}
        </div>
      </div>

      {!isActive && (session.unread_count ?? 0) > 0 && (
        <span className="shrink-0 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-medium leading-none text-white">
          {session.unread_count}
        </span>
      )}
    </div>
  );
}
