import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ShieldCheck, Mail, ExternalLink, ArrowRight } from "lucide-react";
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
  const closeRef = useRef<HTMLButtonElement>(null);

  const adminEmail = null;
  const adminUrl = null;

  useEffect(() => {
    if (isOpen) {
      closeRef.current?.focus();
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (isOpen && e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

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

  const hasContact = adminEmail || adminUrl;

  return createPortal(
    <div
      data-yields-sidebar
      className="fixed inset-0 z-[300] flex items-center justify-center p-4"
    >
      <div
        className="absolute inset-0 bg-[var(--theme-overlay-strong)]"
        onClick={onClose}
      />
      <div className="enterprise-modal-shell relative z-10 max-w-[420px] animate-in fade-in zoom-in-95 duration-200">
        {/* Header illustration */}
        <div className="relative overflow-hidden px-8 pb-7 pt-9">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)]">
            <ShieldCheck className="h-7 w-7 text-amber-500 dark:text-amber-400" />
          </div>
          <div className="text-center">
            <h3 className="text-base font-semibold tracking-tight text-stone-900 dark:text-stone-50">
              {title}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-stone-500 dark:text-stone-400">
              {description}
            </p>
          </div>
        </div>

        {/* Contact methods */}
        <div className="px-5 py-5">
          {hasContact ? (
            <div className="space-y-2.5">
              {adminEmail && (
                <a
                  href={`mailto:${adminEmail}`}
                  className="group enterprise-subtle-panel flex items-center gap-3 px-4 py-3 text-sm text-stone-600 transition-colors hover:bg-[var(--theme-bg-card)] dark:text-stone-300"
                >
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-card)] ring-1 ring-[var(--theme-border)]">
                    <Mail size={15} className="text-stone-400" />
                  </div>
                  <span className="flex-1 truncate">{adminEmail}</span>
                  <ArrowRight
                    size={15}
                    className="text-stone-300 transition-transform group-hover:translate-x-0.5 dark:text-stone-600"
                  />
                </a>
              )}
              {adminUrl && (
                <a
                  href={adminUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group enterprise-subtle-panel flex items-center gap-3 px-4 py-3 text-sm text-stone-600 transition-colors hover:bg-[var(--theme-bg-card)] dark:text-stone-300"
                >
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--theme-bg-card)] ring-1 ring-[var(--theme-border)]">
                    <ExternalLink size={15} className="text-stone-400" />
                  </div>
                  <span className="flex-1">
                    {t("contactAdmin.supportLink", "联系管理员")}
                  </span>
                  <ArrowRight
                    size={15}
                    className="text-stone-300 transition-transform group-hover:translate-x-0.5 dark:text-stone-600"
                  />
                </a>
              )}
            </div>
          ) : (
            <div className="enterprise-subtle-panel px-4 py-4 text-center">
              <p className="text-sm text-stone-400 dark:text-stone-500">
                {t(
                  "contactAdmin.noContactInfo",
                  "暂无管理员联系方式，请联系系统管理员。",
                )}
              </p>
            </div>
          )}
        </div>

        {/* Close */}
        <div className="px-5 pb-6 pt-1">
          <button
            ref={closeRef}
            onClick={onClose}
            className="btn-primary w-full justify-center"
          >
            {t("common.close", "关闭")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
