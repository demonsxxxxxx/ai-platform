export const AUTH_REDIRECT_ANIMATION_MS = 240;
export const AUTH_REDIRECT_FAILSAFE_MS = 4000;

export function resolvePostAuthRedirectPath(
  redirectPath?: string | null,
): string {
  return redirectPath || "/chat";
}
