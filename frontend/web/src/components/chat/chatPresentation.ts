import type { ReactNode } from "react";

/** Product-specific presentation slots; the shared chat owns runtime and Composer state. */
export interface ChatPresentation {
  header: ReactNode;
  renderOpening: (composer: ReactNode) => ReactNode;
  renderStarterPrompts: (disabled: boolean) => ReactNode;
  disableSlashCommands: boolean;
}
