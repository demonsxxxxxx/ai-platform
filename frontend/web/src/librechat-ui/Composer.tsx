import {
  forwardRef,
  type HTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import { clsx } from "clsx";

/** Frames the composer on the shared workbench canvas. */
export function LibreChatComposerFrame({
  children,
  className,
  style,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...props}
      className={clsx(
        "chat-input-shell librechat-composer-shell pb-3 sm:px-4",
        className,
      )}
      data-librechat-composer="phase1"
      style={{ backgroundColor: "var(--theme-workbench-canvas)", ...style }}
    >
      {children}
    </div>
  );
}

/** Wraps composer content in the rounded input container. */
export const LibreChatComposerBox = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement> & {
    dragging?: boolean;
    mentionActive?: boolean;
  }
>(function LibreChatComposerBox(
  { children, className, style, dragging, mentionActive, ...props },
  ref,
) {
  return (
    <div
      {...props}
      ref={ref}
      className={clsx(
        "chat-input-container relative flex w-full flex-col rounded-lg border px-1",
        "transition-all duration-300",
        dragging && "border-2 border-dashed shadow-lg",
        className,
      )}
      data-mention-active={mentionActive || undefined}
      style={{
        backgroundColor: "var(--theme-workbench-panel)",
        borderColor: dragging ? "var(--theme-primary)" : "var(--theme-border)",
        boxShadow: dragging ? undefined : "0 1px 2px rgba(15,23,42,0.04)",
        ...style,
      }}
    >
      {children}
    </div>
  );
});

/** Marks a named composer region for source and browser smoke checks. */
export function LibreChatComposerRegion({
  region,
  children,
  className,
}: {
  region: "attachments" | "chips" | "textarea" | "toolbar";
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-librechat-composer-region={region}
      className={clsx(region === "textarea" && "px-2.5 pt-1", className)}
    >
      {children}
    </div>
  );
}

/** Provides the textarea primitive used by the active chat composer. */
export const LibreChatComposerTextarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function LibreChatComposerTextarea({ className, style, ...props }, ref) {
  return (
    <textarea
      {...props}
      ref={ref}
      className={clsx(
        "w-full resize-none overflow-y-auto bg-transparent pt-[10px]",
        "text-[15px] leading-relaxed outline-none disabled:opacity-50",
        "[&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]",
        "min-h-[40px] sm:min-h-[44px]",
        className,
      )}
      style={{ color: "var(--theme-text)", paddingLeft: 4, ...style }}
    />
  );
});
