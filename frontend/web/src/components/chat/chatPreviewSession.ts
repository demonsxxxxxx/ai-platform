import { registerAuthScopedCacheClearer } from "../../services/api/authCacheInvalidation";
import { closeAttachmentPreview } from "./attachmentPreviewStore";
import { closeBlockPreview } from "./ChatMessage/items/blockPreviewStore";
import { clearSidebarHistory } from "./ChatMessage/items/sidebarHistoryStore";

let currentOwnerKey: string | undefined;

export function isCurrentChatPreviewSession(ownerKey: string): boolean {
  return currentOwnerKey === ownerKey;
}

/** Claim the rendered conversation before paint, retaining same-owner remounts. */
export function claimChatPreviewSession(ownerKey: string): boolean {
  if (currentOwnerKey === ownerKey) return false;
  currentOwnerKey = ownerKey;
  closeAttachmentPreview();
  closeBlockPreview();
  clearSidebarHistory();
  return true;
}

// There is deliberately no unmount cleanup: a departing view must not clear a
// newer view's preview. The stores clear themselves on this same auth boundary.
registerAuthScopedCacheClearer(() => { currentOwnerKey = undefined; });
