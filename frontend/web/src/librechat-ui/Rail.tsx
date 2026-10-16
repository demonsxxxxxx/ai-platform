import { Button } from "@lobehub/ui/base-ui";
import { forwardRef, type ButtonHTMLAttributes, type ComponentProps, type ReactNode } from "react";

interface LibreChatRailButtonProps
  extends Omit<ComponentProps<typeof Button>, "type" | "htmlType"> {
  type?: ButtonHTMLAttributes<HTMLButtonElement>["type"];
  itemKey?: string;
  active?: boolean;
  children: ReactNode;
}

/** Renders a fixed-size rail button for the compact workbench sidebar. */
export const LibreChatRailButton = forwardRef<
  HTMLButtonElement,
  LibreChatRailButtonProps
>(function LibreChatRailButton(
  { itemKey, active = false, children, className = "", type = "button", ...props },
  ref,
) {
  return (
    <Button type="text"
      {...props}
      htmlType={type}
      size="large"
      icon={<>{children}</>}
      ref={ref}
      data-active={active ? "true" : "false"}
      data-workbench-rail-item={itemKey}
      className={`sidebar-rail-btn workbench-rail-btn mx-1 touch-manipulation ${className}`}
    />
  );
});
