import {
  LibreChatShell,
  type LibreChatShellProps,
} from "../librechatShell/LibreChatShell";

export type WorkbenchShellProps = LibreChatShellProps;

export function WorkbenchShell(props: WorkbenchShellProps) {
  return <LibreChatShell {...props} />;
}
