import { useCallback } from "react";
import { channelAdminApi } from "../services/api/channel";

/** Exposes audited channel admin operations only when channel:admin is granted. */
export function useChannelAdminOperations({ enabled }: { enabled: boolean }) {
  const testAdminChannel = useCallback(
    (channelId: string, workspaceId = "default") => {
      if (!enabled) {
        throw new Error("missing_permission:channel:admin");
      }
      return channelAdminApi.testAdminChannel(channelId, workspaceId);
    },
    [enabled],
  );

  return { enabled, testAdminChannel };
}
