import type { DepartmentDirectoryNode } from "../../services/api/capabilityDistribution";

export interface DepartmentDirectoryOption extends DepartmentDirectoryNode {
  depth: number;
}

export interface DepartmentSelectionResolution {
  resolved: DepartmentDirectoryOption[];
  unresolvedAuthorityIds: string[];
  authoritative: boolean;
}

export function flattenDepartmentDirectory(
  nodes: DepartmentDirectoryNode[],
  depth = 0,
): DepartmentDirectoryOption[] {
  return nodes.flatMap((node) => [
    { ...node, depth },
    ...flattenDepartmentDirectory(node.children, depth + 1),
  ]);
}

export function resolveDepartmentSelection(
  authorityIds: string[],
  nodes: DepartmentDirectoryNode[] | null,
): DepartmentSelectionResolution {
  if (authorityIds.length === 0) {
    return { resolved: [], unresolvedAuthorityIds: [], authoritative: true };
  }
  if (nodes === null) {
    return {
      resolved: [],
      unresolvedAuthorityIds: [...authorityIds],
      authoritative: false,
    };
  }

  const byAuthorityId = new Map<string, DepartmentDirectoryOption[]>();
  for (const option of flattenDepartmentDirectory(nodes)) {
    const matches = byAuthorityId.get(option.authorityId) ?? [];
    matches.push(option);
    byAuthorityId.set(option.authorityId, matches);
  }

  const seenAuthorityIds = new Set<string>();
  const duplicateAuthorityIds = new Set<string>();
  for (const authorityId of authorityIds) {
    if (seenAuthorityIds.has(authorityId)) duplicateAuthorityIds.add(authorityId);
    seenAuthorityIds.add(authorityId);
  }

  const resolved: DepartmentDirectoryOption[] = [];
  const unresolvedAuthorityIds: string[] = [];
  for (const authorityId of authorityIds) {
    if (duplicateAuthorityIds.has(authorityId)) {
      if (!unresolvedAuthorityIds.includes(authorityId)) {
        unresolvedAuthorityIds.push(authorityId);
      }
      continue;
    }
    const matches = byAuthorityId.get(authorityId) ?? [];
    if (matches.length === 1 && matches[0].selectable) {
      resolved.push(matches[0]);
    } else {
      unresolvedAuthorityIds.push(authorityId);
    }
  }
  return {
    resolved,
    unresolvedAuthorityIds,
    authoritative: unresolvedAuthorityIds.length === 0,
  };
}
