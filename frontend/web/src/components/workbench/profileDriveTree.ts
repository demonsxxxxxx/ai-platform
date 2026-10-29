import type { ProfileDriveFileEntry } from "../../services/api/profileDrive";

export interface LoadedProfileDriveDirectory {
  entries: ProfileDriveFileEntry[];
  truncated: boolean;
}

export type ProfileDriveTreeRow =
  | { kind: "entry"; entry: ProfileDriveFileEntry; chain: ProfileDriveFileEntry[]; ancestors: ProfileDriveFileEntry[]; depth: number }
  | { kind: "status"; key: string; depth: number; text: string }
  | { kind: "error"; key: string; depth: number; entry: ProfileDriveFileEntry; text: string };

/** Project only known listings; never read more directories to compact a chain. */
export function buildProfileDriveTree({ entries, directories, expanded, loading, errors, compact, focusTrail, filter }: {
  entries: ProfileDriveFileEntry[];
  directories: ReadonlyMap<string, LoadedProfileDriveDirectory>;
  expanded: ReadonlySet<string>;
  loading: ReadonlySet<string>;
  errors: ReadonlyMap<string, string>;
  compact: boolean;
  focusTrail: ProfileDriveFileEntry[];
  filter: string;
}): ProfileDriveTreeRow[] {
  const rows: ProfileDriveTreeRow[] = [];
  const query = filter.trim().toLocaleLowerCase();
  function contents(entry: ProfileDriveFileEntry, ancestors: ProfileDriveFileEntry[], depth: number) {
    const key = entry.path;
    if (loading.has(key)) rows.push({ kind: "status", key: `${key}:loading`, depth, text: "正在加载…" });
    else if (errors.has(key)) rows.push({ kind: "error", key: `${key}:error`, entry, depth, text: errors.get(key)! });
    else {
      const loaded = directories.get(key);
      if (!loaded) return;
      if (!loaded.entries.length) rows.push({ kind: "status", key: `${key}:empty`, depth, text: "目录为空" });
      else append(loaded.entries, ancestors, depth);
      if (loaded.truncated) rows.push({ kind: "status", key: `${key}:truncated`, depth, text: "目录内容已截断" });
    }
  }
  function append(children: ProfileDriveFileEntry[], ancestors: ProfileDriveFileEntry[], depth: number) {
    for (const first of children) {
      const chain = [first];
      let entry = first;
      while (compact && !query && entry.type === "directory" && expanded.has(entry.path) && !loading.has(entry.path) && !errors.has(entry.path)) {
        const loaded = directories.get(entry.path);
        if (!loaded || loaded.truncated || loaded.entries.length !== 1 || loaded.entries[0].type !== "directory") break;
        entry = loaded.entries[0];
        chain.push(entry);
      }
      rows.push({ kind: "entry", entry, chain, ancestors, depth });
      if (entry.type === "directory" && expanded.has(entry.path)) contents(entry, [...ancestors, ...chain], depth + 1);
    }
  }
  const focused = focusTrail.at(-1);
  if (focused) contents(focused, focusTrail, 0);
  else append(entries, [], 0);
  return rows;
}
