import { useState, useCallback } from "react";
import { Copy, Check } from "lucide-react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";
import toast from "react-hot-toast";
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
    toast.success(t("chat.message.copied"));
    setTimeout(() => setCopied(false), 2000);
  }, [text, t]);

  return (
    <button
      onClick={handleCopy}
      className={clsx(
        "grid place-items-center w-7 h-7 rounded-md transition-all touch-manipulation shrink-0",
        copied
          ? "text-green-600 dark:text-green-400"
          : "text-stone-400 hover:text-stone-600 hover:bg-stone-200/50 dark:text-stone-500 dark:hover:text-stone-300 dark:hover:bg-stone-700/50",
        className,
      )}
      title={
        label
          ? copied
            ? t("chat.message.copied")
            : label
          : copied
            ? t("chat.message.copied")
            : t("chat.message.copy")
      }
    >
      {copied ? <Check size={size} /> : <Copy size={size} />}
    </button>
  );
}
