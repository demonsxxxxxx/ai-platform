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
      className={`sidebar-rail-btn workbench-rail-btn flex h-11 w-11 items-center justify-center rounded-lg text-slate-200 transition-colors mx-1 touch-manipulation ${className}`}
    >
      {children}
    </button>
  );
});
