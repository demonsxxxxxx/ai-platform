import { Button, Modal } from "@lobehub/ui/base-ui";
import { ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ContactAdminDialogProps {
  isOpen: boolean;
  onClose: () => void;
  reason?: "noPermission" | "emailActivation";
}

export function ContactAdminDialog({
  isOpen,
  onClose,
  reason = "noPermission",
}: ContactAdminDialogProps) {
  const { t } = useTranslation();
  const title =
    reason === "emailActivation"
      ? t("contactAdmin.emailActivationTitle", "邮箱验证问题")
      : t("contactAdmin.noPermissionTitle", "权限不足");

  const description =
    reason === "emailActivation"
      ? t(
          "contactAdmin.emailActivationDesc",
          "您的邮箱尚未验证或验证链接已过期，请联系管理员获取帮助。",
        )
      : t(
          "contactAdmin.noPermissionDesc",
          "您当前没有发送消息的权限。如需开通，请联系管理员。",
        );

  return (
    <Modal open={isOpen} onCancel={onClose} title={title} width={420}
      draggable={false} closable={false} classNames={{ wrapper: "workbench-modal" }}
      footer={<Button type="primary" onClick={onClose} autoFocus>{t("common.close", "关闭")}</Button>}
    >
      <div className="flex items-start gap-3">
        <ShieldCheck aria-hidden="true" size={24} className="shrink-0 text-[var(--theme-warning-text)]" />
        <div className="space-y-3 text-sm leading-relaxed text-[var(--theme-text-secondary)]">
          <p>{description}</p>
          <p>{t("contactAdmin.noContactInfo", "暂无管理员联系方式，请联系系统管理员。")}</p>
        </div>
      </div>
    </Modal>
  );
}
