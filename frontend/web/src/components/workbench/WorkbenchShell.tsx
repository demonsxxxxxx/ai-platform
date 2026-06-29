import {
  LibreChatShell,
  type LibreChatShellProps,
} from "../../librechat-ui/Shell";

export type WorkbenchShellProps = LibreChatShellProps;

export function WorkbenchShell(props: WorkbenchShellProps) {
  return <LibreChatShell {...props} />;
}
