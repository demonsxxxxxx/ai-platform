import {
  LibreChatSidePanel,
  type LibreChatSidePanelProps,
} from "../../librechat-ui/SidePanel";
import type { SessionInputFile } from "../../services/api";
import type { ProfileDriveFileReference } from "../../services/api/profileDrive";
import { ProfileDriveWorkspaceBrowser } from "./ProfileDriveWorkspaceBrowser";

export interface WorkbenchRightPanelProps
  extends Omit<LibreChatSidePanelProps, "additionalSections"> {
  sessionId: string | null;
  onProfileDriveFileImported: (file: SessionInputFile) => void;
  onProfileDriveFileDrop: (
    reference: ProfileDriveFileReference,
  ) => void | Promise<void>;
}

export function WorkbenchRightPanel({
  sessionId,
  onProfileDriveFileImported,
  onProfileDriveFileDrop,
  ...props
}: WorkbenchRightPanelProps) {
  return (
    <LibreChatSidePanel
      {...props}
      additionalSections={
        <>
          <ProfileDriveWorkspaceBrowser
            key={`${sessionId ?? "no-session"}-profile`}
            sessionId={sessionId}
            sourceId="profile"
            title="个人文件"
            onImported={onProfileDriveFileImported}
            onAddToConversation={onProfileDriveFileDrop}
          />
          <ProfileDriveWorkspaceBrowser
            key={`${sessionId ?? "no-session"}-public`}
            sessionId={sessionId}
            sourceId="public"
            title="公盘"
            onImported={onProfileDriveFileImported}
            onAddToConversation={onProfileDriveFileDrop}
          />
        </>
      }
    />
  );
}
