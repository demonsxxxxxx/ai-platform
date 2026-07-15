/** One decision can start only when neither a refresh nor another decision owns the inbox. */
export function isInboxDecisionDisabled(
  isLoading: boolean,
  decidingId: string | null,
): boolean {
  return isLoading || decidingId !== null;
}

/** Build a collision-safe owner key only for an explicitly governed auth subject. */
export function governanceInboxSubjectKey(
  user: { id?: string; tenant_id?: string } | null,
  canGovern: boolean,
): string | null {
  const tenantId = String(user?.tenant_id || "").trim();
  const userId = String(user?.id || "").trim();
  if (!canGovern || !tenantId || !userId) return null;
  return JSON.stringify([tenantId, userId]);
}
