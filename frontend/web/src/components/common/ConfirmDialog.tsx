import { Modal } from "@lobehub/ui/base-ui";
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
  return (
    <Modal
      open={isOpen}
      title={title}
      width={400}
      draggable={false}
      closable={false}
      keyboard={!loading}
      maskClosable={!loading}
      onCancel={() => { if (!loading) onCancel(); }}
      onOk={onConfirm}
      confirmLoading={loading}
      okText={confirmText || t("common.confirm")}
      cancelText={cancelText || t("common.cancel")}
      cancelButtonProps={{ disabled: loading }}
      okButtonProps={{ autoFocus: true, disabled: loading, danger: variant === "danger" }}
      classNames={{ wrapper: "workbench-modal" }}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden="true" size={22} className={variant === "danger" ? "shrink-0 text-[var(--theme-danger)]" : variant === "warning" ? "shrink-0 text-amber-500" : "shrink-0 text-[var(--theme-primary)]"} />
        <p className="text-sm leading-relaxed text-[var(--theme-text-secondary)]">{message}</p>
      </div>
    </Modal>
  );
}
