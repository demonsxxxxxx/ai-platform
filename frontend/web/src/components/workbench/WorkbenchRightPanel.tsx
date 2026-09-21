import {
  LibreChatSidePanel,
  type LibreChatSidePanelProps,
} from "../../librechat-ui/SidePanel";
import type { SessionInputFile } from "../../services/api";
import { ProfileDriveWorkspaceBrowser } from "./ProfileDriveWorkspaceBrowser";

export interface WorkbenchRightPanelProps
  extends Omit<LibreChatSidePanelProps, "additionalSections"> {
  sessionId: string | null;
  onProfileDriveFileImported: (file: SessionInputFile) => void;
}

export function WorkbenchRightPanel({
  sessionId,
  onProfileDriveFileImported,
  ...props
}: WorkbenchRightPanelProps) {
  return (
    <LibreChatSidePanel
      {...props}
      additionalSections={
        <ProfileDriveWorkspaceBrowser
          key={sessionId ?? "no-session"}
          sessionId={sessionId}
          onImported={onProfileDriveFileImported}
        />
      }
    />
  );
}
