import { MessageNotSentError } from "@assistant-ui/react";
import type { ChatInputDraftSnapshot, ChatInputProps } from "../chatInputTypes";
import { prepareSelectedSkillSubmission } from "../../../hooks/useSelectedSkillTask";
import { releaseChatInputSubmissionLock, tryAcquireChatInputSubmissionLock } from "../chatInputSubmissionLock";
import type { MessageAttachment } from "../../../types";

type SubmissionContext = Pick<ChatInputProps,
  "onSend" | "agentOptionValues" | "selectedSkillState" | "onSelectedSkillRecoverable" | "onClearSelectedSkill"
> & {
  content: string;
  snapshot: ChatInputDraftSnapshot;
  attachments: MessageAttachment[];
  allowed: boolean;
  lock: { current: symbol | null };
  setAttachments: (next: (current: MessageAttachment[]) => MessageAttachment[]) => void;
  setInput: (value: string) => void;
  pushHistory: (value: string) => void;
  restoreText: (value: string) => void;
};

/** One transaction for native Composer send and keyboard submission. */
export async function submitComposerMessage({
  content, snapshot, attachments, allowed, lock, onSend, agentOptionValues,
  selectedSkillState, onSelectedSkillRecoverable, onClearSelectedSkill,
  setAttachments, setInput, pushHistory, restoreText,
}: SubmissionContext): Promise<void> {
  if (!allowed || !content.trim()) throw new MessageNotSentError();
  const revision = snapshot.revision;
  const scopeRevision = snapshot.scopeRevision ?? 0;
  const isCurrentOwner = () => (snapshot.scopeRevision ?? 0) === scopeRevision;
  const skill = selectedSkillState
    ? prepareSelectedSkillSubmission(selectedSkillState, attachments)
    : { error: null, request: null };
  if (skill.error) {
    await onSelectedSkillRecoverable?.(skill.error);
    if (isCurrentOwner() && snapshot.apply && snapshot.revision === revision) {
      restoreText("");
      throw new MessageNotSentError();
    }
    return;
  }
  const token = tryAcquireChatInputSubmissionLock(lock);
  if (!token) throw new MessageNotSentError();
  const skillRevision = snapshot.selectedSkillRevision;
  const attachmentIds = new Set(attachments.map((attachment) => attachment.id));
  if (snapshot.scopeKey == null) snapshot.pendingScopeHandoff = true;
  let nativeWillRestore = false;
  try {
    const outcome = await onSend(content.trim(), agentOptionValues, attachments, skill.request);
    if (!isCurrentOwner()) return;
    if (outcome.status === "recoverable_error") {
      await onSelectedSkillRecoverable?.(outcome.code);
      if (!isCurrentOwner()) return;
    }
    if (outcome.status !== "accepted") {
      if (snapshot.apply && snapshot.revision === revision) {
        nativeWillRestore = true;
        // Remount may have restored the same draft while this receipt was pending.
        restoreText("");
        throw new MessageNotSentError();
      }
      return;
    }
    if (outcome.status === "accepted") {
      pushHistory(content.trim());
      if (snapshot.revision === revision) setInput("");
      setAttachments((current) => current.filter((attachment) => !attachmentIds.has(attachment.id)));
      if (snapshot.selectedSkillRevision === skillRevision) onClearSelectedSkill?.();
    }
  } finally {
    releaseChatInputSubmissionLock(lock, token);
    // Native rejection restores its draft and attachments; restoring here too duplicates text.
    if (!nativeWillRestore && isCurrentOwner() && snapshot.apply) restoreText(snapshot.value);
  }
}
