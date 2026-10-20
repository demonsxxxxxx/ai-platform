// Adapted from assistant-ui Elements thread.aui.tsx action bar at 727a02dc (MIT).
import { ActionBarPrimitive, MessageProvider, fromThreadMessageLike, useAui, useAuiState } from "@assistant-ui/react";
import { Button, toast } from "@lobehub/ui/base-ui";
import { Check, Copy } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { copyToClipboard } from "../../../utils/clipboard";

function CopyMessageButton() {
  const { t } = useTranslation();
  const aui = useAui();
  const copied = useAuiState((state) => state.message.isCopied);
  return (
    <ActionBarPrimitive.Copy asChild copiedDuration={2000} onClick={(event) => {
      // HTTP intranet pages still need the platform's non-Clipboard-API copy path.
      if (navigator.clipboard) return;
      event.preventDefault();
      void copyToClipboard(aui.message.getCopyText())
        .then(() => toast.success(t("chat.message.copied")))
        .catch(() => toast.error(t("common.error")));
    }}>
      <Button type="text" htmlType="button" size="small"
        aria-label={copied ? t("chat.message.copied") : t("chat.message.copy")}
        title={copied ? t("chat.message.copied") : t("chat.message.copy")}
        style={{ color: copied ? "var(--theme-success)" : "var(--theme-text-secondary)" }}
        icon={copied ? <Check size={16} /> : <Copy size={16} />} />
    </ActionBarPrimitive.Copy>
  );
}

/** Scope native copy to the public answer receipt, excluding work and pending previews. */
export function AssistantUiMessageActions({
  messageId, text, children,
}: { messageId: string; text: string; children?: ReactNode }) {
  const message = useMemo(() => fromThreadMessageLike({
    id: messageId, role: "assistant", content: text,
    status: { type: "complete", reason: "stop" },
  }, messageId, { type: "complete", reason: "stop" }), [messageId, text]);
  return (
    <MessageProvider message={message} index={0}>
      <ActionBarPrimitive.Root hideWhenRunning className="flex items-center gap-1" data-slot="aui-message-actions" data-assistant-ui-message-actions>
        {text && <CopyMessageButton />}
        {children}
      </ActionBarPrimitive.Root>
    </MessageProvider>
  );
}
