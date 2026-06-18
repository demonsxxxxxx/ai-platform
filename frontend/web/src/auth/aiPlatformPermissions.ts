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

export function normalizePrincipalPermissions(
  rawPermissions: readonly string[] | null | undefined,
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
