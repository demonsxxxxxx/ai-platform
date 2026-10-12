type RevealedArtifactSource = "reveal_file" | "reveal_project";

export interface ExternalNavigationTargetFile {
  fileId?: string;
  fileKey?: string | null;
  fileName?: string;
  originalPath?: string | null;
  traceId?: string | null;
  source?: RevealedArtifactSource;
}

export interface ExternalNavigationState {
  externalNavigate?: boolean;
  scrollToBottom?: boolean;
  targetFile?: ExternalNavigationTargetFile | null;
}

export function shouldResetExternalNavigateFlag(
  locationState: ExternalNavigationState | null | undefined,
): boolean {
  return locationState?.externalNavigate === true;
}

export function shouldScrollToBottomAfterExternalNavigation(
  locationState: ExternalNavigationState | null | undefined,
): boolean {
  return (
    locationState?.externalNavigate === true &&
    locationState?.scrollToBottom === true
  );
}

export function getExternalNavigationTargetFile(
  locationState: ExternalNavigationState | null | undefined,
): ExternalNavigationTargetFile | null {
  if (locationState?.externalNavigate !== true) {
    return null;
  }

  const targetFile = locationState.targetFile;
  if (!targetFile) {
    return null;
  }

  const sanitizedTargetFile: ExternalNavigationTargetFile = {
    ...(targetFile.fileId ? { fileId: targetFile.fileId } : {}),
    ...(targetFile.traceId?.trim() ? { traceId: targetFile.traceId } : {}),
    ...(targetFile.source ? { source: targetFile.source } : {}),
  };

  const hasMatchableField =
    !!sanitizedTargetFile.fileId || !!sanitizedTargetFile.traceId?.trim();

  return hasMatchableField ? sanitizedTargetFile : null;
}
