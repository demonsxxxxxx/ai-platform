import {
  downloadPreviewUrl as defaultDownloadPreviewUrl,
  type DownloadPreviewUrlOptions,
} from "../../../documents/documentPreviewSources";
import { isAllowedRevealArtifactUrl } from "./revealPreviewData";

export interface ArtifactDownloadInput {
  download_url?: string | null;
  downloadUrl?: string | null;
  label: string;
}

type ArtifactDownloadPreviewUrl = (
  input: DownloadPreviewUrlOptions,
) => Promise<void>;

export async function downloadArtifactFile(
  artifact: ArtifactDownloadInput,
  options: {
    downloadPreviewUrl?: ArtifactDownloadPreviewUrl;
    downloadOptions?: Omit<DownloadPreviewUrlOptions, "url" | "fileName">;
  } = {},
): Promise<boolean> {
  const url = (artifact.download_url ?? artifact.downloadUrl)?.trim();
  if (!url || !isAllowedRevealArtifactUrl(url)) {
    return false;
  }

  const downloadPreviewUrl =
    options.downloadPreviewUrl ?? defaultDownloadPreviewUrl;
  await downloadPreviewUrl({
    ...options.downloadOptions,
    url,
    fileName: artifact.label,
  });
  return true;
}
