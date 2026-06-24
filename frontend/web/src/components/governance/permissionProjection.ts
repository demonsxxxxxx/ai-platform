import { Permission } from "../../types";

const inheritedPermissions: Partial<Record<Permission, Permission[]>> = {
  [Permission.ROLE_MANAGE]: [Permission.ROLE_READ, Permission.ROLE_REQUEST],
  [Permission.ROLE_REQUEST]: [Permission.ROLE_READ],
  [Permission.USER_ADMIN]: [Permission.USER_READ],
  [Permission.SETTINGS_ADMIN]: [
    Permission.SETTINGS_READ,
    Permission.SETTINGS_MANAGE,
  ],
  [Permission.FEEDBACK_ADMIN]: [Permission.FEEDBACK_READ],
  [Permission.NOTIFICATION_ADMIN]: [
    Permission.NOTIFICATION_READ,
    Permission.NOTIFICATION_MANAGE,
  ],
  [Permission.SKILL_ADMIN]: [
    Permission.SKILL_READ,
    Permission.SKILL_WRITE,
    Permission.SKILL_DELETE,
  ],
  [Permission.MARKETPLACE_ADMIN]: [
    Permission.MARKETPLACE_READ,
    Permission.MARKETPLACE_PUBLISH,
  ],
};

export function hasEffectivePermission(
  grantedPermissions: readonly Permission[],
  permission: Permission,
): boolean {
  if (grantedPermissions.includes(permission)) {
    return true;
  }
  return grantedPermissions.some((granted) =>
    inheritedPermissions[granted]?.includes(permission),
  );
}

export function hasAnyEffectivePermission(
  grantedPermissions: readonly Permission[],
  permissions: readonly Permission[],
): boolean {
  return permissions.some((permission) =>
    hasEffectivePermission(grantedPermissions, permission),
  );
}

export function hasAllEffectivePermissions(
  grantedPermissions: readonly Permission[],
  permissions: readonly Permission[],
): boolean {
  return permissions.every((permission) =>
    hasEffectivePermission(grantedPermissions, permission),
  );
}
