export const PROFILE_DRIVE_DRAG_TYPE =
  "application/x-ai-platform-profile-drive-path";

export function hasProfileDriveDragData(
  dataTransfer: Pick<DataTransfer, "types">,
): boolean {
  return Array.from(dataTransfer.types).includes(PROFILE_DRIVE_DRAG_TYPE);
}

export function getProfileDriveDragPath(
  dataTransfer: Pick<DataTransfer, "getData">,
): string | null {
  return dataTransfer.getData(PROFILE_DRIVE_DRAG_TYPE).trim() || null;
}
