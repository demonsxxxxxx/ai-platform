import type { MessagePart } from "../../../types";
import { assistantTextPartRole } from "../../../types/assistantTextParts";

/** Follow the latest visible content phase, not only the Run terminal flag.
 * The caller supplies the existing presentation owner's work classification.
 */
export function shouldExpandWorkActivity(
  parts: readonly MessagePart[],
  isStreaming: boolean | undefined,
  isWorkActivity: (part: MessagePart) => boolean,
): boolean {
  if (!isStreaming) return false;
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const part = parts[index];
    if (isWorkActivity(part)) return true;
    if (part.type === "text" && !part.depth && part.content.trim()) {
      const role = assistantTextPartRole(part);
      if (role === null) continue;
      return false;
    }
  }
  return true;
}
