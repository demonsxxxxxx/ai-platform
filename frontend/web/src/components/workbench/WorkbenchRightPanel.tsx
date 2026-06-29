import {
  LibreChatSidePanel,
  type LibreChatSidePanelProps,
} from "../../librechat-ui/SidePanel";

export type WorkbenchRightPanelProps = LibreChatSidePanelProps;

export function WorkbenchRightPanel(props: WorkbenchRightPanelProps) {
  return <LibreChatSidePanel {...props} />;
}
