import { useEffect } from "react";

import { BROWSER_AUTH_INCARNATION_EVENT } from "../../hooks/browserAuthCoordinator";
import {
  installMcpAuthHandoff,
  installMcpAuthHandoffLifecycle,
} from "../../utils/mcpGatewayAuth";

export type McpAuthHandoffInstaller = () => () => void;

export function McpAuthHandoffLifecycle({
  installer = installMcpAuthHandoff,
}: {
  installer?: McpAuthHandoffInstaller;
}) {
  useEffect(
    () => installMcpAuthHandoffLifecycle(BROWSER_AUTH_INCARNATION_EVENT, installer),
    [installer],
  );
  return null;
}
