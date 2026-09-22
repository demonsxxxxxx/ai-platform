import type { ProfileDriveFileReference } from "../../services/api/profileDrive";

export const PROFILE_DRIVE_DRAG_TYPE =
  "application/x-ai-platform-profile-drive-path";

export function hasProfileDriveDragData(
  dataTransfer: Pick<DataTransfer, "types">,
): boolean {
  return Array.from(dataTransfer.types).includes(PROFILE_DRIVE_DRAG_TYPE);
}

export function serializeProfileDriveDragReference(
  reference: ProfileDriveFileReference,
): string {
  return JSON.stringify(reference);
}

export function getProfileDriveDragReference(
  dataTransfer: Pick<DataTransfer, "getData">,
): ProfileDriveFileReference | null {
  const value = dataTransfer.getData(PROFILE_DRIVE_DRAG_TYPE).trim();
  if (!value) return null;
  if (!value.startsWith("{")) return { source_id: "profile", path: value };
  try {
    const parsed: unknown = JSON.parse(value);
    if (
      parsed &&
      typeof parsed === "object" &&
      !Array.isArray(parsed) &&
      ["profile", "public"].includes(
        String((parsed as Record<string, unknown>).source_id),
      ) &&
      typeof (parsed as Record<string, unknown>).path === "string" &&
      (parsed as Record<string, unknown>).path
    ) {
      const sourceId = String(
        (parsed as Record<string, unknown>).source_id,
      );
      const path = (parsed as Record<string, unknown>).path;
      return {
        source_id: sourceId as ProfileDriveFileReference["source_id"],
        path: path as string,
      };
    }
  } catch {
    return null;
  }
  return null;
}
