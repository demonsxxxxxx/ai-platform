import {
  buildArtifactPreviewRequest,
  type ArtifactPreviewInput,
} from "../../chat/ChatMessage/items/artifactPreview";
import { setActiveRevealPreviewState } from "../../chat/ChatMessage/items/activeRevealPreviewStore";
import {
  createActiveRevealPreviewState,
  type ActiveRevealPreviewState,
  type RevealPreviewOpenSource,
} from "../../chat/ChatMessage/items/revealPreviewState";
import type { RunPlaybackArtifactItem } from "./runPlaybackPanelState";

type RunPlaybackArtifactPreviewSetter = (
  next: ActiveRevealPreviewState,
) => void;

export function buildRunPlaybackArtifactPreviewRequest(
  artifact: RunPlaybackArtifactItem,
) {
  const input: ArtifactPreviewInput = {
    artifact_id: artifact.id,
    label: artifact.label,
    content_type: artifact.contentType,
    download_url: artifact.downloadUrl,
    preview_url: artifact.previewUrl,
  };
  return buildArtifactPreviewRequest(input);
}

export function openRunPlaybackArtifactPreview(
  artifact: RunPlaybackArtifactItem,
  options: {
    source?: RevealPreviewOpenSource;
    setPreviewState?: RunPlaybackArtifactPreviewSetter;
  } = {},
): boolean {
  const request = buildRunPlaybackArtifactPreviewRequest(artifact);
  if (!request) {
    return false;
  }

  const source = options.source ?? "manual";
  const setPreviewState = options.setPreviewState ?? setActiveRevealPreviewState;
  setPreviewState(createActiveRevealPreviewState(request, source));
  return true;
}
