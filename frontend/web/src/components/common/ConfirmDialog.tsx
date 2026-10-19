import { Button, ModalBackdrop, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalPopup, ModalPortal, ModalRoot, ModalTitle } from "@lobehub/ui/base-ui";
import { useLayoutEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel: () => void;
  variant?: "danger" | "warning" | "info";
  loading?: boolean;
}

export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmText,
  cancelText,
  onConfirm,
  onCancel,
  variant = "danger",
  loading = false,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (isOpen && document.activeElement instanceof HTMLElement) {
      returnFocusRef.current = document.activeElement;
    }
  }, [isOpen]);
  return (
    <ModalRoot open={isOpen} onOpenChange={(open) => { if (!open && !loading) onCancel(); }}>
      <ModalPortal>
        <ModalBackdrop />
        <ModalPopup width={400} className="workbench-modal" initialFocus={confirmRef} finalFocus={returnFocusRef}>
          <ModalHeader><ModalTitle>{title}</ModalTitle></ModalHeader>
          <ModalContent>
            <div className="flex items-start gap-3">
              <AlertTriangle aria-hidden="true" size={22} className={variant === "danger" ? "shrink-0 text-[var(--theme-danger)]" : variant === "warning" ? "shrink-0 text-amber-500" : "shrink-0 text-[var(--theme-primary)]"} />
              <ModalDescription className="text-sm leading-relaxed text-[var(--theme-text-secondary)]">{message}</ModalDescription>
            </div>
          </ModalContent>
          <ModalFooter>
            <Button disabled={loading} onClick={onCancel}>{cancelText || t("common.cancel")}</Button>
            <Button ref={confirmRef} type="primary" danger={variant === "danger"} loading={loading} disabled={loading} onClick={onConfirm}>
              {confirmText || t("common.confirm")}
            </Button>
          </ModalFooter>
        </ModalPopup>
      </ModalPortal>
    </ModalRoot>
  );
}
