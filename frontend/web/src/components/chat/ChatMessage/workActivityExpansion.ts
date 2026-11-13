/** Initial operation disclosure only; subsequent content never resets user choice. */
export function shouldExpandWorkActivity(
  isStreaming: boolean | undefined,
): boolean {
  return isStreaming === true;
}
