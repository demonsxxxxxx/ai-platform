export interface ClosableLike {
  contains: (target: Node) => boolean;
}

export function shouldCloseModelSelector(
  target: Node | null,
  container: ClosableLike | null,
  dropdown: ClosableLike | null,
): boolean {
  if (!target) return false;
  if (container?.contains(target)) return false;
  if (dropdown?.contains(target)) return false;
  return true;
}
