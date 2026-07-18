import { getFullUrl } from "../../../../services/api/config";
import { registerAuthScopedCacheClearer } from "../../../../services/api/authCacheInvalidation";
import {
  fetchDocumentText,
  shouldUseAuthenticatedDocumentRequest,
  type DocumentFetchOptions,
} from "../../../documents/documentFetchCache";
import { resolveDocumentPreviewUrl } from "../../../documents/documentPreviewSources";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "../../../documents/documentUrlSafety";
import { rewriteProjectTextFiles } from "./projectRevealAssetUtils";

export { isSensitiveInternalPath };

export type ProjectTemplate =
  | "react"
  | "vue"
  | "vanilla"
  | "static"
  | "angular"
  | "svelte"
  | "solid"
  | "nextjs";

export type ProjectRevealMode = "project" | "folder";

export interface FileManifestEntry {
  url: string;
  is_binary: boolean;
  size: number;
  content_type?: string;
}

interface ProjectRevealResultBase {
  type: "project_reveal";
  name: string;
  description?: string;
  template: ProjectTemplate;
  mode?: ProjectRevealMode;
  entry?: string;
  path?: string;
  file_count?: number;
  error?: string;
  message?: string;
}

export interface ProjectRevealResultV1 extends ProjectRevealResultBase {
  files: Record<string, string>;
}

export interface ProjectRevealResultV2 extends ProjectRevealResultBase {
  version: 2;
  files: Record<string, FileManifestEntry>;
}

export type ParsedProjectRevealData =
  | {
      version: 1;
      name: string;
      mode: ProjectRevealMode;
      template: ProjectTemplate;
      entry?: string;
      path?: string;
      fileCount: number;
      files: Record<string, string>;
    }
  | {
      version: 2;
      name: string;
      mode: ProjectRevealMode;
      template: ProjectTemplate;
      entry?: string;
      path?: string;
      fileCount: number;
      files: Record<string, FileManifestEntry>;
    };

export interface ParsedProjectRevealSummary {
  projectName: string;
  mode: ProjectRevealMode;
  template: ProjectTemplate;
  error: string;
  fileCount: number;
  projectPath: string;
  parsed: ParsedProjectRevealData | null;
}

export type RevealPreviewRequest =
  | {
      kind: "file";
      previewKey: string;
      filePath: string;
      content?: string;
      s3Key?: string;
      signedUrl?: string;
      previewUrl?: string;
      downloadUrl?: string;
      imageUrl?: string;
      fileSize?: number;
      mimeType?: string;
    }
  | {
      kind: "project";
      previewKey: string;
      project: ParsedProjectRevealData;
      openInFullscreen?: boolean;
    };

export interface ParsedFileRevealPreviewData {
  filePath: string;
  description: string;
  s3Key: string;
  s3Url: string;
  fileSize?: number;
  mimeType?: string;
  error: string;
}

interface FileRevealResultNew {
  key?: unknown;
  url?: unknown;
  name?: unknown;
  type?: unknown;
  mimeType?: unknown;
  size?: unknown;
  _meta?: {
    path?: unknown;
    description?: unknown;
  };
}

interface FileRevealResultOld {
  type?: unknown;
  file?: {
    path?: unknown;
    description?: unknown;
    s3_url?: unknown;
    s3_key?: unknown;
    size?: unknown;
    error?: unknown;
  };
}

export interface ProjectRevealMetaInput {
  mode?: ProjectRevealMode;
  template?: ProjectTemplate;
  entry?: string;
  file_count?: number;
  files: Record<
    string,
    {
      url: string;
      size: number;
      is_binary?: boolean;
      content_type?: string;
    }
  >;
}

function isProjectRevealV2(
  result: ProjectRevealResultV1 | ProjectRevealResultV2,
): result is ProjectRevealResultV2 {
  if ("version" in result && result.version === 2) return true;
  const firstFile = Object.values(result.files)[0];
  return (
    typeof firstFile === "object" && firstFile !== null && "url" in firstFile
  );
}

function normalizeString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeFileSize(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

export function sanitizeProjectPath(value: unknown): string {
  if (typeof value !== "string") return "";
  return isSensitiveInternalPath(value) ? "" : value;
}

export function sanitizeProjectEntry(value: unknown): string | undefined {
  const sanitized = sanitizeProjectPath(value);
  return sanitized || undefined;
}

export function isAllowedRevealArtifactUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || isSensitiveInternalPath(trimmed)) return false;
  if (!shouldUseAuthenticatedDocumentRequest(trimmed)) return false;
  return isAllowedAuthenticatedArtifactFileUrl(trimmed);
}

function sanitizeRevealKey(value: unknown): string {
  const key = normalizeString(value);
  return key && !isSensitiveInternalPath(key) ? key : "";
}

function sanitizeRevealUrl(value: unknown): string {
  const url = normalizeString(value);
  if (!url || !isAllowedRevealArtifactUrl(url)) return "";
  return getFullUrl(url) || url;
}

function parseJsonishRevealResult(
  result: string | Record<string, unknown> | undefined,
): Record<string, unknown> | null {
  if (!result) return null;
  if (typeof result === "object") return result;

  try {
    let jsonStr = result;
    const contentMatch = result.match(/content='(.+?)'(\s|$)/);
    if (contentMatch) jsonStr = contentMatch[1].replace(/\\'/g, "'");
    return JSON.parse(jsonStr) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function parseFileRevealPreviewData(input: {
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
}): ParsedFileRevealPreviewData {
  const fallbackPath = sanitizeProjectPath(input.args.path);
  const fallbackDescription = normalizeString(input.args.description);
  const parsed: ParsedFileRevealPreviewData = {
    filePath: fallbackPath,
    description: fallbackDescription,
    s3Key: "",
    s3Url: "",
    error: "",
  };

  const raw = parseJsonishRevealResult(input.result);
  if (!raw) return parsed;

  if ("key" in raw || "url" in raw) {
    const reveal = raw as FileRevealResultNew;
    const metaPath = reveal._meta?.path;
    const hasMetaPath = typeof metaPath === "string";
    const filePath = hasMetaPath
      ? sanitizeProjectPath(metaPath)
      : sanitizeProjectPath(reveal.name);
    const s3Url = sanitizeRevealUrl(reveal.url);
    const pathIsSensitive = hasMetaPath && !filePath;

    return {
      filePath,
      description: normalizeString(reveal._meta?.description),
      s3Key: pathIsSensitive || !s3Url ? "" : sanitizeRevealKey(reveal.key),
      s3Url: pathIsSensitive ? "" : s3Url,
      fileSize: normalizeFileSize(reveal.size),
      mimeType: normalizeString(reveal.mimeType) || undefined,
      error: "",
    };
  }

  if (raw.type === "file_reveal" && typeof raw.file === "object") {
    const reveal = raw as FileRevealResultOld;
    const file = reveal.file || {};
    const filePath = sanitizeProjectPath(file.path);
    const s3Url = sanitizeRevealUrl(file.s3_url);
    const pathIsSensitive = !!normalizeString(file.path) && !filePath;

    return {
      filePath,
      description: normalizeString(file.description),
      s3Key: pathIsSensitive || !s3Url ? "" : sanitizeRevealKey(file.s3_key),
      s3Url: pathIsSensitive ? "" : s3Url,
      fileSize: normalizeFileSize(file.size),
      error: normalizeString(file.error),
    };
  }

  return parsed;
}

function sanitizeFileManifestEntry(
  entry: unknown,
): FileManifestEntry | null {
  if (!entry || typeof entry !== "object") return null;
  const record = entry as Record<string, unknown>;
  if (
    typeof record.url !== "string" ||
    !isAllowedRevealArtifactUrl(record.url)
  ) {
    return null;
  }

  const sanitized: FileManifestEntry = {
    url: record.url.trim(),
    is_binary: record.is_binary === true,
    size: typeof record.size === "number" && Number.isFinite(record.size)
      ? record.size
      : 0,
  };

  if (typeof record.content_type === "string" && record.content_type.trim()) {
    sanitized.content_type = record.content_type;
  }

  return sanitized;
}

function sanitizeFileManifest(
  files: Record<string, FileManifestEntry>,
): Record<string, FileManifestEntry> {
  const sanitizedFiles: Record<string, FileManifestEntry> = {};
  for (const [path, entry] of Object.entries(files || {})) {
    if (isSensitiveInternalPath(path)) continue;
    const sanitizedEntry = sanitizeFileManifestEntry(entry);
    if (sanitizedEntry) {
      sanitizedFiles[path] = sanitizedEntry;
    }
  }
  return sanitizedFiles;
}

function sanitizeTextFileMap(
  files: Record<string, string>,
): Record<string, string> {
  const sanitizedFiles: Record<string, string> = {};
  for (const [path, content] of Object.entries(files || {})) {
    if (isSensitiveInternalPath(path)) continue;
    if (typeof content !== "string") continue;
    sanitizedFiles[path] = content;
  }
  return sanitizedFiles;
}

export function buildSanitizedProjectRevealDataFromMeta(input: {
  name: string;
  path?: string | null;
  meta?: ProjectRevealMetaInput | null;
}): Extract<ParsedProjectRevealData, { version: 2 }> | null {
  if (!input.meta?.files) return null;

  const summary = parseProjectRevealSummary({
    args: {},
    result: {
      type: "project_reveal",
      version: 2,
      name: input.name,
      mode: input.meta.mode ?? "project",
      path: input.path ?? undefined,
      template: input.meta.template ?? "vanilla",
      entry: input.meta.entry,
      file_count: input.meta.file_count,
      files: input.meta.files,
    },
    parseErrorMessage: "parse error",
  });

  return summary.parsed?.version === 2 ? summary.parsed : null;
}

export function parseProjectRevealSummary(input: {
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  parseErrorMessage: string;
}): ParsedProjectRevealSummary {
  const { args, result, parseErrorMessage } = input;
  let projectName = "";
  let mode: ProjectRevealMode = "project";
  let template: ProjectTemplate = "vanilla";
  let error = "";
  let fileCount = 0;
  let projectPath = "";
  let parsed: ParsedProjectRevealData | null = null;

  if (result) {
    try {
      const raw =
        typeof result === "string"
          ? (JSON.parse(result) as
              | ProjectRevealResultV1
              | ProjectRevealResultV2)
          : (result as unknown as
              | ProjectRevealResultV1
              | ProjectRevealResultV2);

      if (raw.error) {
        error = raw.message || raw.error;
      } else if (isProjectRevealV2(raw)) {
        const sanitizedFiles = sanitizeFileManifest(raw.files);
        const visibleFileCount = Object.keys(sanitizedFiles).length;
        projectName = raw.name || "";
        projectPath = sanitizeProjectPath(raw.path);
        mode = raw.mode || "project";
        template = raw.template || "vanilla";
        fileCount = visibleFileCount;
        parsed = {
          version: 2,
          name: projectName,
          mode,
          ...(projectPath ? { path: projectPath } : {}),
          template,
          entry: sanitizeProjectEntry(raw.entry),
          fileCount,
          files: sanitizedFiles,
        };
      } else {
        const sanitizedFiles = sanitizeTextFileMap(raw.files);
        const visibleFileCount = Object.keys(sanitizedFiles).length;
        projectName = raw.name || "";
        projectPath = sanitizeProjectPath(raw.path);
        mode = raw.mode || "project";
        template = raw.template || "vanilla";
        fileCount = visibleFileCount;
        parsed = {
          version: 1,
          name: projectName,
          mode,
          ...(projectPath ? { path: projectPath } : {}),
          template,
          entry: sanitizeProjectEntry(raw.entry),
          fileCount,
          files: sanitizedFiles,
        };
      }
    } catch {
      error = parseErrorMessage;
    }
  } else {
    projectName = (args.name as string) || "";
  }

  return {
    projectName,
    mode,
    template,
    error,
    fileCount,
    projectPath,
    parsed,
  };
}

type ResolveProjectRevealPreviewUrl = typeof resolveDocumentPreviewUrl;

interface LoadProjectRevealFilesOptions {
  fetchOptions?: DocumentFetchOptions;
  resolvePreviewUrl?: ResolveProjectRevealPreviewUrl;
}

function revokeBlobUrl(url: string): void {
  if (url.startsWith("blob:") && typeof URL !== "undefined") {
    URL.revokeObjectURL(url);
  }
}

export async function loadProjectRevealFiles(
  project: Extract<ParsedProjectRevealData, { version: 2 }>,
  options: LoadProjectRevealFilesOptions = {},
): Promise<{
  files: Record<string, string>;
  binaryFiles: Record<string, string>;
  failed: string[];
}> {
  const textEntries: Array<[string, FileManifestEntry]> = [];
  const binaryFiles: Record<string, string> = {};
  const resolvePreviewUrl = options.resolvePreviewUrl ?? resolveDocumentPreviewUrl;

  await Promise.all(
    Object.entries(project.files).map(async ([path, entry]) => {
      const fullUrl = getFullUrl(entry.url) || entry.url;
      if (entry.is_binary) {
        try {
          binaryFiles[path] = await resolvePreviewUrl({
            url: fullUrl,
            mimeType: entry.content_type || "application/octet-stream",
            fetchOptions: options.fetchOptions,
          });
        } catch (error) {
          console.warn(`[reveal_project] Error resolving ${path}:`, error);
        }
        return;
      }
      textEntries.push([path, { ...entry, url: fullUrl }]);
    }),
  );

  const entries = await Promise.all(
    textEntries.map(async ([path, entry]): Promise<[string, string] | null> => {
      try {
        const text = await fetchDocumentText(entry.url, options.fetchOptions);
        return [path, text];
      } catch (error) {
        console.warn(`[reveal_project] Error fetching ${path}:`, error);
        return null;
      }
    }),
  );

  const rawFiles: Record<string, string> = {};
  const failed: string[] = [];
  for (const entry of entries) {
    if (entry) {
      rawFiles[entry[0]] = entry[1];
    }
  }

  for (const [path] of textEntries) {
    if (!(path in rawFiles)) {
      failed.push(path);
    }
  }

  return {
    files: rewriteProjectTextFiles(rawFiles, binaryFiles),
    binaryFiles,
    failed,
  };
}

export function shouldShowProjectRevealLoadingError(input: {
  files: Record<string, string>;
  binaryFiles: Record<string, string>;
  manifestFiles: Record<string, FileManifestEntry>;
}): boolean {
  return (
    Object.keys(input.files).length === 0 &&
    Object.keys(input.binaryFiles).length === 0 &&
    Object.keys(input.manifestFiles).length > 0
  );
}

type LoadedProjectRevealFiles = Awaited<
  ReturnType<typeof loadProjectRevealFiles>
>;

const loadedProjectRevealFilesCache = new Map<
  string,
  LoadedProjectRevealFiles
>();
const inflightProjectRevealFilesCache = new Map<
  string,
  Promise<LoadedProjectRevealFiles>
>();
let projectRevealFilesCacheGeneration = 0;

export function getCachedProjectRevealFiles(
  previewKey: string | null | undefined,
): LoadedProjectRevealFiles | null {
  if (!previewKey) return null;
  return loadedProjectRevealFilesCache.get(previewKey) || null;
}

export async function loadProjectRevealFilesCached(input: {
  previewKey: string;
  project: Extract<ParsedProjectRevealData, { version: 2 }>;
}): Promise<LoadedProjectRevealFiles> {
  const { previewKey, project } = input;
  const cached = loadedProjectRevealFilesCache.get(previewKey);
  if (cached) {
    return cached;
  }

  const inflight = inflightProjectRevealFilesCache.get(previewKey);
  if (inflight) {
    return inflight;
  }

  const generation = projectRevealFilesCacheGeneration;
  const request = loadProjectRevealFiles(project)
    .then((result) => {
      if (generation === projectRevealFilesCacheGeneration) {
        loadedProjectRevealFilesCache.set(previewKey, result);
      }
      if (
        generation === projectRevealFilesCacheGeneration &&
        inflightProjectRevealFilesCache.get(previewKey) === request
      ) {
        inflightProjectRevealFilesCache.delete(previewKey);
      }
      return result;
    })
    .catch((error) => {
      if (
        generation === projectRevealFilesCacheGeneration &&
        inflightProjectRevealFilesCache.get(previewKey) === request
      ) {
        inflightProjectRevealFilesCache.delete(previewKey);
      }
      throw error;
    });

  inflightProjectRevealFilesCache.set(previewKey, request);
  return request;
}

export function clearProjectRevealFilesCache(): void {
  projectRevealFilesCacheGeneration += 1;
  for (const result of loadedProjectRevealFilesCache.values()) {
    Object.values(result.binaryFiles).forEach(revokeBlobUrl);
  }
  loadedProjectRevealFilesCache.clear();
  inflightProjectRevealFilesCache.clear();
}

registerAuthScopedCacheClearer(clearProjectRevealFilesCache);
