export function isSensitiveInternalPath(value: string): boolean {
  const maxDecodeAttempts = 8;
  const sensitiveMarkers = [
    "runtime",
    "runtimepath",
    "workdir",
    "storagekey",
    "commandsha256",
    "usedskillssource",
    "resourcelimits",
  ];
  const seen = new Set<string>();
  let current = value;

  for (let attempt = 0; attempt <= maxDecodeAttempts; attempt += 1) {
    if (seen.has(current)) break;
    seen.add(current);

    const normalized = current.replace(/\\/g, "/").toLowerCase();
    const compact = normalized.replace(/[^a-z0-9]+/g, "");
    if (
      /(^|\/)\.claude(\/|$)/.test(normalized) ||
      sensitiveMarkers.some((marker) => compact.includes(marker))
    ) {
      return true;
    }

    if (attempt === maxDecodeAttempts) break;

    try {
      const decoded = decodeURIComponent(current);
      if (decoded === current) break;
      current = decoded;
    } catch {
      break;
    }
  }

  return false;
}

function getCurrentOrigin(): string | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.location.origin;
}

function hasAbsoluteScheme(value: string): boolean {
  return /^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith("//");
}

function isAllowedArtifactFilePath(pathname: string): boolean {
  if (pathname === "/api/upload/file" || pathname.startsWith("/api/upload/file/")) {
    return true;
  }

  if (!pathname.startsWith("/api/ai/artifacts/")) {
    return false;
  }

  const segments = pathname.split("/").filter(Boolean);
  if (segments.length < 5) {
    return false;
  }
  const action = segments[segments.length - 1];
  return action === "download" || action === "preview";
}

export function isAllowedAuthenticatedArtifactFileUrl(
  value: string,
  options: { currentOrigin?: string } = {},
): boolean {
  const trimmed = value.trim();
  if (!trimmed || isSensitiveInternalPath(trimmed)) return false;

  const currentOrigin = options.currentOrigin ?? getCurrentOrigin();
  try {
    const parsed = new URL(trimmed, currentOrigin ?? "http://localhost");
    if (isSensitiveInternalPath(`${parsed.pathname}${parsed.search}`)) {
      return false;
    }
    if (!isAllowedArtifactFilePath(parsed.pathname)) {
      return false;
    }
    if (hasAbsoluteScheme(trimmed)) {
      return Boolean(currentOrigin) && parsed.origin === currentOrigin;
    }
    return true;
  } catch {
    return false;
  }
}
