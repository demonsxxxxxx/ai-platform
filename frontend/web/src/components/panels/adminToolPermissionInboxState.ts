/** One decision can start only when neither a refresh nor another decision owns the inbox. */
export function isInboxDecisionDisabled(
  isLoading: boolean,
  decidingId: string | null,
): boolean {
  return isLoading || decidingId !== null;
}
