import type { User } from "../../types";

const AI_ADMIN_ROLE_ALIASES = new Set([
  "admin",
  "developer",
  "platform_admin",
  "break_glass_admin",
]);

export function isAiAdminRoleUser(
  roles: readonly string[] | null | undefined,
): boolean {
  return (roles ?? []).some((role) =>
    AI_ADMIN_ROLE_ALIASES.has(role.trim().toLowerCase()),
  );
}

export function isAiAdminUser(
  user: Pick<User, "roles"> & { is_admin?: boolean } | null | undefined,
): boolean {
  if (!user) {
    return false;
  }
  return user.is_admin === true || isAiAdminRoleUser(user.roles);
}

export function canManageSharedMarketplace({
  isOwner,
  hasMarketplaceAdminPermission,
  isAiAdmin,
}: {
  isOwner: boolean;
  hasMarketplaceAdminPermission: boolean;
  isAiAdmin: boolean;
}): boolean {
  void isOwner;
  void hasMarketplaceAdminPermission;
  return isAiAdmin;
}

export function canManageMcpLifecycle({
  hasExplicitMcpPermission,
  isAiAdmin,
}: {
  hasExplicitMcpPermission: boolean;
  isAiAdmin: boolean;
}): boolean {
  void hasExplicitMcpPermission;
  return isAiAdmin;
}
