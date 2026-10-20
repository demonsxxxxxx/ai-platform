import { createContext, useContext } from "react";
import type { AttachmentAdapter } from "@assistant-ui/react";

/** Platform submission and draft rules behind assistant-ui's composer actions. */
export interface PlatformComposerBinding {
  send: (content: string) => Promise<void>;
  requestCancel: () => void;
  setText: (content: string) => void;
  getText: () => string;
  disabled: boolean;
  sendDisabled: boolean;
  canCancel: boolean;
  attachmentAdapter?: AttachmentAdapter;
}

export const PlatformComposerContext = createContext<{
  register: (binding: PlatformComposerBinding) => () => void;
} | null>(null);

export function usePlatformComposer() {
  const context = useContext(PlatformComposerContext);
  if (!context) throw new Error("ChatInput requires AssistantUiProjection");
  return context;
}
