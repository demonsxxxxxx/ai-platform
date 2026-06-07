export interface ClosableLike {
  contains: (target: Node) => boolean;
}

export function shouldCloseTokenDetailsPopover(
  target: Node | null,
  trigger: ClosableLike | null,
  popup: ClosableLike | null,
): boolean {
  if (!target) return false;
  if (trigger?.contains(target)) return false;
  if (popup?.contains(target)) return false;
  return true;
}
