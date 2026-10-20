import { useMemo, type ReactNode } from "react";
import type { ThreadSuggestion } from "@assistant-ui/react";
import { AssistantUiProjection, type AssistantUiProjectionProps } from "../AssistantUiProjection";
import type { Message } from "../../../../types";

const noopActions = {
  sendMessage: async () => {}, cancel: async () => {},
  reconnect: async () => {}, loadHistory: async () => {},
};

export function AssistantUiTestProvider({ children, isRunning = false, messages = [], suggestions = [], actions: overrides }: {
  children?: ReactNode; isRunning?: boolean; messages?: Message[];
  suggestions?: readonly ThreadSuggestion[];
  actions?: Partial<AssistantUiProjectionProps["actions"]>;
}) {
  // Stable identity: a new actions object on every render would rebuild the runtime.
  const sendMessage = overrides?.sendMessage;
  const cancel = overrides?.cancel;
  const reconnect = overrides?.reconnect;
  const loadHistory = overrides?.loadHistory;
  const actions = useMemo(
    () => ({
      sendMessage: sendMessage ?? noopActions.sendMessage,
      cancel: cancel ?? noopActions.cancel,
      reconnect: reconnect ?? noopActions.reconnect,
      loadHistory: loadHistory ?? noopActions.loadHistory,
    }),
    [cancel, loadHistory, reconnect, sendMessage],
  );
  return <AssistantUiProjection messages={messages} isRunning={isRunning}
    actions={actions} suggestions={suggestions}>{children}</AssistantUiProjection>;
}
