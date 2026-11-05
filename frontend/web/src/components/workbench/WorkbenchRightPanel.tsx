import { useContext } from "react";
import { CloseContextPanelContext } from "../../librechat-ui/ContextPanel";
import { LibreChatSidePanel } from "../../librechat-ui/SidePanel";
import type { SessionInputFile } from "../../services/api";
import type { MessageAttachment } from "../../types";
import type { ProfileDriveFileReference } from "../../services/api/profileDrive";
import { ProfileDriveWorkspaceBrowser } from "./ProfileDriveWorkspaceBrowser";

export interface WorkbenchRightPanelProps {
  sessionId: string | null;
  attachments: readonly MessageAttachment[];
  onRemoveAttachment: (attachment: MessageAttachment) => void;
  onProfileDriveFileImported: (file: SessionInputFile) => void;
  onProfileDriveFileDrop: (
    reference: ProfileDriveFileReference,
  ) => SessionInputFile | void | Promise<SessionInputFile | void>;
}

export function WorkbenchRightPanel({
  sessionId,
  onProfileDriveFileImported,
  onProfileDriveFileDrop,
  attachments,
  onRemoveAttachment,
}: WorkbenchRightPanelProps) {
  const closeContext = useContext(CloseContextPanelContext);
  return (
    <LibreChatSidePanel
      additionalSections={
        <ProfileDriveWorkspaceBrowser
          key={sessionId ?? "no-session"}
          sessionId={sessionId}
          onImported={(file) => { onProfileDriveFileImported(file); closeContext(); }}
          onAddToConversation={onProfileDriveFileDrop}
          attachments={attachments}
          onRemoveAttachment={onRemoveAttachment}
        />
      }
    />
  );
}
