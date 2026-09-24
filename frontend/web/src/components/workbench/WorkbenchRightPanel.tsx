import { LibreChatSidePanel } from "../../librechat-ui/SidePanel";
import type { SessionInputFile } from "../../services/api";
import type { ProfileDriveFileReference } from "../../services/api/profileDrive";
import { ProfileDriveWorkspaceBrowser } from "./ProfileDriveWorkspaceBrowser";

export interface WorkbenchRightPanelProps {
  sessionId: string | null;
  onProfileDriveFileImported: (file: SessionInputFile) => void;
  onProfileDriveFileDrop: (
    reference: ProfileDriveFileReference,
  ) => SessionInputFile | void | Promise<SessionInputFile | void>;
}

export function WorkbenchRightPanel({
  sessionId,
  onProfileDriveFileImported,
  onProfileDriveFileDrop,
}: WorkbenchRightPanelProps) {
  return (
    <LibreChatSidePanel
      additionalSections={
        <ProfileDriveWorkspaceBrowser
          key={sessionId ?? "no-session"}
          sessionId={sessionId}
          onImported={onProfileDriveFileImported}
          onAddToConversation={onProfileDriveFileDrop}
        />
      }
    />
  );
}
