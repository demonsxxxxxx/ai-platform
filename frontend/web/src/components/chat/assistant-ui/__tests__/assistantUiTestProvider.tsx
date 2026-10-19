import type { ReactNode } from "react";
import { AssistantUiProjection } from "../AssistantUiProjection";
import type { Message } from "../../../../types";

const actions = {
  sendMessage: async () => {}, cancel: async () => {},
  reconnect: async () => {}, loadHistory: async () => {},
};

export function AssistantUiTestProvider({ children, isRunning = false, messages = [] }: {
  children?: ReactNode; isRunning?: boolean; messages?: Message[];
}) {
  return <AssistantUiProjection messages={messages} isRunning={isRunning} actions={actions}>{children}</AssistantUiProjection>;
}
