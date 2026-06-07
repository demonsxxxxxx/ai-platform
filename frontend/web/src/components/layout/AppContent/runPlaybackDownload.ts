import {
  downloadPreviewUrl as defaultDownloadPreviewUrl,
  type DownloadPreviewUrlOptions,
} from "../../documents/documentPreviewSources";
import { isAllowedAuthenticatedArtifactFileUrl } from "../../documents/documentUrlSafety";
import type { RunPlaybackArtifactItem } from "./runPlaybackPanelState";

type RunPlaybackArtifactDownloader = (
  input: DownloadPreviewUrlOptions,
) => Promise<void>;

export async function downloadRunPlaybackArtifact(
  artifact: Pick<RunPlaybackArtifactItem, "downloadUrl" | "label">,
  options: {
    downloadPreviewUrl?: RunPlaybackArtifactDownloader;
    downloadOptions?: Omit<DownloadPreviewUrlOptions, "url" | "fileName">;
  } = {},
): Promise<boolean> {
  if (!artifact.downloadUrl) {
    return false;
  }
  if (!isAllowedAuthenticatedArtifactFileUrl(artifact.downloadUrl)) {
    return false;
  }
  const downloadPreviewUrl =
    options.downloadPreviewUrl ?? defaultDownloadPreviewUrl;
  await downloadPreviewUrl({
    ...options.downloadOptions,
    url: artifact.downloadUrl,
    fileName: artifact.label,
  });
  return true;
}
