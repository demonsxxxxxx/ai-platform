export function getUserMessageActionButtonVisibilityClass(
  isLastMessage?: boolean,
) {
  return isLastMessage ? "" : "opacity-0 group-hover:opacity-100";
}
