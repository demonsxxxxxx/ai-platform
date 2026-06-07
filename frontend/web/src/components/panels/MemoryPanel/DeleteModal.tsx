import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "../../common/ConfirmDialog";

export function DeleteModal({
  onConfirm,
  onCancel,
  count = 1,
}: {
  onConfirm: () => void;
  onCancel: () => void;
  count?: number;
}) {
  const { t } = useTranslation();

  return (
    <ConfirmDialog
      isOpen
      title={t("memory.deleteConfirm")}
      message={
        count > 1
          ? t("memory.batchDeleteConfirmMessage", { count })
          : t("memory.deleteConfirmMessage")
      }
      confirmText={t("common.delete")}
      cancelText={t("common.cancel")}
      onConfirm={onConfirm}
      onCancel={onCancel}
      variant="danger"
    />
  );
}
