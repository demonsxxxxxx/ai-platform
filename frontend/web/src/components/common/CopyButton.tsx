import { Button } from "@lobehub/ui/base-ui";
import { useState, useCallback } from "react";
import { Copy, Check } from "lucide-react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";
import { toast } from "@lobehub/ui/base-ui";
import { copyToClipboard } from "../../utils/clipboard";

export function CopyButton({
  text,
  size = 14,
  className,
  label,
}: {
  text: string;
  size?: number;
  className?: string;
  label?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!text) return;
    await copyToClipboard(text);
    setCopied(true);
    toast.success({ description: t("chat.message.copied"), hideCloseButton: true });
    setTimeout(() => setCopied(false), 2000);
  }, [text, t]);

  return (
    <Button type="text"
      onClick={handleCopy}
      className={clsx("touch-manipulation shrink-0", className)}
      icon={copied ? <Check size={size} className="text-[var(--theme-success)]" /> : <Copy size={size} />}
      title={
        label
          ? copied
            ? t("chat.message.copied")
            : label
          : copied
            ? t("chat.message.copied")
            : t("chat.message.copy")
      }
    />
  );
}
