import { Tooltip } from "@lobehub/ui/base-ui";
import type { ReactElement } from "react";

export function AttachmentFilenameTooltip({ name, children }: { name: string; children: ReactElement }) {
  return (
    <Tooltip
      popupContainer={typeof document === "undefined" ? undefined : document.body}
      classNames={{ root: "attachment-filename-tooltip" }}
      title={<span aria-hidden="true" className="wrap-anywhere">{name}</span>}
    >
      {children}
    </Tooltip>
  );
}
