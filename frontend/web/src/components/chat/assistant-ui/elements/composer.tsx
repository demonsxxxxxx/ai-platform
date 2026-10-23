// Adapted from assistant-ui Elements composer.tsx and thread.aui.tsx at 727a02dc (MIT).
import { forwardRef, type ComponentProps, type TextareaHTMLAttributes } from "react";
import { ComposerPrimitive } from "@assistant-ui/react";
import { Button, TextArea, PopoverRoot, PopoverPortal, PopoverPositioner, PopoverPopup } from "@lobehub/ui/base-ui";
import { ArrowUp, Square } from "lucide-react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";

export function Composer({ className, ...props }: ComponentProps<typeof ComposerPrimitive.Root>) {
  return <ComposerPrimitive.Root data-slot="composer" className={clsx("aui-composer-root relative mx-auto flex w-full max-w-[76rem] flex-col px-2", className)} {...props} />;
}

export const ComposerBar = forwardRef<HTMLDivElement, ComponentProps<"div"> & { dragActive?: boolean }>(
  function ComposerBar({ dragActive, className, style, ...props }, ref) {
    return <div ref={ref} data-slot="composer-bar" data-drag-active={dragActive || undefined}
      className={clsx("chat-input-container flex w-full flex-col gap-2 rounded-2xl border p-2.5 transition-colors", className)}
      style={{ background: "var(--theme-workbench-panel)", borderColor: dragActive ? "var(--theme-primary)" : "var(--theme-border)", borderStyle: dragActive ? "dashed" : "solid", ...style }} {...props} />;
  },
);

export const ComposerInput = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function ComposerInput({ className, style, ...props }, ref) {
    return <ComposerPrimitive.Input asChild submitMode="none" cancelOnEscape={false} addAttachmentOnPaste={false}
      unstable_focusOnRunStart={false} unstable_focusOnThreadSwitched={false} ref={ref} {...props}>
      <TextArea variant="borderless" className="w-full" style={{ padding: 0 }}
        classNames={{ input: clsx("aui-composer-input min-h-[48px] max-h-48 resize-none overflow-y-auto px-2.5 py-1 text-[15px] leading-6", className) }}
        styles={{ input: style }} />
    </ComposerPrimitive.Input>;
  },
);

export function ComposerToolbar({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="composer-toolbar" className={clsx("flex min-h-11 items-center justify-between gap-2", className)} {...props} />;
}

export function ComposerActions({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="composer-actions" className={clsx("flex shrink-0 items-center gap-1.5", className)} {...props} />;
}

export function ComposerSubmit({ running, canStop, canSubmit, uploading }: {
  running: boolean; canStop: boolean; canSubmit: boolean; uploading: boolean;
}) {
  const { t } = useTranslation();
  if (running) return canStop ? (
    <ComposerPrimitive.Cancel asChild>
      <Button data-slot="composer-stop" type="default" shape="circle" size="large" htmlType="button"
        aria-label={t("chat.stop")} title={t("chat.stop")} icon={<Square size={16} fill="currentColor" />} />
    </ComposerPrimitive.Cancel>
  ) : <Button type="default" shape="circle" size="large" htmlType="button" disabled loading
    aria-label={t("chat.generating")} title={t("chat.generating")} icon={<Square size={16} />} />;
  return <ComposerPrimitive.Send asChild onClick={(event) => {
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }}>
    <Button data-slot="composer-send" type={canSubmit ? "primary" : "default"} shape="circle" size="large" htmlType="button" disabled={!canSubmit}
      aria-label={t("chat.send")} icon={<ArrowUp size={18} />}
      title={uploading ? t("chat.waitingForUpload") : t("chat.send")} />
  </ComposerPrimitive.Send>;
}

/** Controlled command view; the platform owns slash parsing and keyboard selection. */
export function ComposerMenu({ children, label, onClose }: {
  children: React.ReactNode; label: string; onClose: () => void;
}) {
  return <PopoverRoot open onOpenChange={(open) => { if (!open) onClose(); }}>
    <PopoverPortal>
      <PopoverPositioner placement="topLeft" anchor={() => document.querySelector<HTMLElement>('[data-slot="composer-bar"]')}>
        <PopoverPopup initialFocus={false} finalFocus={false} role="listbox" aria-label={label}
          data-slot="composer-menu" data-composer-command-menu
          style={{ width: 360, maxWidth: "calc(100vw - 32px)", maxHeight: "min(60dvh, 24rem)", overflowY: "auto", padding: 6 }}>
          <div className="px-3 py-2 text-xs text-[var(--theme-text-secondary)]">{label}</div>
          {children}
        </PopoverPopup>
      </PopoverPositioner>
    </PopoverPortal>
  </PopoverRoot>;
}

export function ComposerMenuItem({ active, style, ...props }: ComponentProps<typeof Button> & { active?: boolean }) {
  return <Button type="text" htmlType="button" block data-slot="composer-menu-item" data-active={active ? "" : undefined}
    style={{ height: "auto", minHeight: 44, justifyContent: "flex-start", textAlign: "left", padding: "8px 10px", background: active ? "var(--theme-bg-sidebar)" : undefined, ...style }} {...props} />;
}
