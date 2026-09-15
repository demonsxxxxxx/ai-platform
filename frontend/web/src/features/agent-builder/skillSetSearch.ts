import type { PublicSkillResponse } from "../../types";

/** Filter the authorized Skill catalog without exposing internal version ids as search data. */
export function filterSkillCatalog(
  skills: readonly PublicSkillResponse[],
  query: string,
): PublicSkillResponse[] {
  const normalizedQuery = query.trim().normalize("NFKC").toLocaleLowerCase();
  if (!normalizedQuery) return [...skills];
  return skills.filter((skill) =>
    [skill.name, skill.description, ...skill.tags].some((value) =>
      value.normalize("NFKC").toLocaleLowerCase().includes(normalizedQuery),
    ),
  );
}
