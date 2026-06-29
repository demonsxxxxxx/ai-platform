import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

interface LibreChatRailButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  itemKey?: string;
  active?: boolean;
  children: ReactNode;
}

export const LibreChatRailButton = forwardRef<
  HTMLButtonElement,
  LibreChatRailButtonProps
>(function LibreChatRailButton(
  { itemKey, active = false, children, className = "", ...props },
  ref,
) {
  return (
    <button
      {...props}
      ref={ref}
      data-active={active ? "true" : "false"}
      data-workbench-rail-item={itemKey}
      className={`sidebar-rail-btn workbench-rail-btn mx-1 flex h-10 w-10 touch-manipulation items-center justify-center rounded-md text-[var(--theme-text-secondary)] transition-colors ${className}`}
    >
      {children}
    </button>
  );
});
