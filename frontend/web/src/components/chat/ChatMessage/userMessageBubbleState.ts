export function getUserMessageActionButtonVisibilityClass(
  isLastMessage?: boolean,
) {
  return isLastMessage ? "" : "opacity-0 group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100";
}
