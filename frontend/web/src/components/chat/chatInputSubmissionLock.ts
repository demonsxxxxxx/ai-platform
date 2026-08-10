export interface ChatInputSubmissionLock {
  current: symbol | null;
}

export function tryAcquireChatInputSubmissionLock(
  lock: ChatInputSubmissionLock,
): symbol | null {
  if (lock.current !== null) return null;
  const token = Symbol("chat-input-submission");
  lock.current = token;
  return token;
}

export function releaseChatInputSubmissionLock(
  lock: ChatInputSubmissionLock,
  token: symbol,
): void {
  if (lock.current === token) lock.current = null;
}

export function reconcileChatInputSubmissionLock(
  lock: ChatInputSubmissionLock,
  isLoading: boolean,
): void {
  if (!isLoading) lock.current = null;
}
