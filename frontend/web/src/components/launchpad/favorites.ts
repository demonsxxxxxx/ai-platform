export const LAUNCHPAD_FAVORITES_METADATA_KEY =
  "company_navigation_favorite_ids";

export function parseLaunchpadFavoriteIds(
  value: unknown,
  allowedIds: ReadonlySet<string>,
): string[] {
  if (!Array.isArray(value)) return [];

  return [
    ...new Set(
      value.filter(
        (entryId): entryId is string =>
          typeof entryId === "string" && allowedIds.has(entryId),
      ),
    ),
  ];
}
