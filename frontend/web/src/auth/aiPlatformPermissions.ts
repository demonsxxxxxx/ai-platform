import { Permission } from "../types/auth";

const KNOWN_PERMISSIONS = new Set<string>(Object.values(Permission));

const DERIVED_PERMISSIONS: Partial<Record<Permission, Permission[]>> = {
  [Permission.AGENT_USE]: [
    Permission.CHAT_READ,
    Permission.CHAT_WRITE,
    Permission.SESSION_READ,
    Permission.SESSION_WRITE,
    Permission.AGENT_READ,
    Permission.FILE_UPLOAD,
    Permission.FILE_UPLOAD_DOCUMENT,
  ],
};

const ADMIN_ROLE_ALIASES = new Set([
  "admin",
  "developer",
  "platform_admin",
  "break_glass_admin",
]);

const ADMIN_SURFACE_PERMISSIONS = [
  Permission.ADMIN_STATUS,
  Permission.AGENT_ADMIN,
  Permission.MODEL_ADMIN,
  Permission.SETTINGS_MANAGE,
];

export function normalizePrincipalPermissions(
  rawPermissions: readonly string[] | null | undefined,
  roles: readonly string[] | null | undefined = [],
  isAdmin = false,
): Permission[] {
  const normalized: Permission[] = [];
  const seen = new Set<Permission>();

  const addPermission = (permission: Permission) => {
    if (seen.has(permission)) return;
    seen.add(permission);
    normalized.push(permission);
  };

  for (const rawPermission of rawPermissions ?? []) {
    const permission = rawPermission.trim();
    if (!KNOWN_PERMISSIONS.has(permission)) continue;
    addPermission(permission as Permission);
  }

  const hasAdminRole = (roles ?? []).some((role) =>
    ADMIN_ROLE_ALIASES.has(role.trim().toLowerCase()),
  );
  if (isAdmin || hasAdminRole) {
    for (const permission of ADMIN_SURFACE_PERMISSIONS) {
      addPermission(permission);
    }
  }

  for (const permission of [...normalized]) {
    for (const derived of DERIVED_PERMISSIONS[permission] ?? []) {
      addPermission(derived);
    }
  }

  return normalized;
}

export function hasEffectivePermission(
  permissions: readonly Permission[],
  permission: Permission,
): boolean {
  return permissions.includes(permission);
}
