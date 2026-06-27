import {
  LibreChatSidePanel,
  type LibreChatSidePanelProps,
} from "../librechatShell/LibreChatSidePanel";

export type WorkbenchRightPanelProps = LibreChatSidePanelProps;

export function WorkbenchRightPanel(props: WorkbenchRightPanelProps) {
  return <LibreChatSidePanel {...props} />;
}
