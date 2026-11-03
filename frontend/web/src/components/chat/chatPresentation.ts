import type { ReactNode } from "react";
import type { AssistantPublicIdentity } from "./ChatMessage";

/** Product-specific presentation slots; the shared chat owns runtime and Composer state. */
export interface ChatPresentation {
  identity: AssistantPublicIdentity;
  header: ReactNode;
  renderOpening: (composer: ReactNode) => ReactNode;
  renderStarterPrompts: (disabled: boolean) => ReactNode;
  disableSlashCommands: boolean;
}
