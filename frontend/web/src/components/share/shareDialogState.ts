import type { ShareType } from "../../types";

interface ShouldLoadRunsOptions {
  isOpen: boolean;
  shareType: ShareType;
  hasLoadedRuns: boolean;
  isLoadingRuns: boolean;
}

interface ShouldShowExistingSharesSkeletonOptions {
  isLoading: boolean;
  hasLoadedShares: boolean;
}

export function shouldLoadRunsForShareType({
  isOpen,
  shareType,
  hasLoadedRuns,
  isLoadingRuns,
}: ShouldLoadRunsOptions): boolean {
  if (!isOpen || shareType !== "partial") {
    return false;
  }

  return !hasLoadedRuns && !isLoadingRuns;
}

export function shouldShowExistingSharesSkeleton({
  isLoading,
  hasLoadedShares,
}: ShouldShowExistingSharesSkeletonOptions): boolean {
  return isLoading && hasLoadedShares;
}
