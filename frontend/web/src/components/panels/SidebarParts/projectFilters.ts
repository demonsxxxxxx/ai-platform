import type { Project } from "../../../types";

export function isSidebarProject(project: Project): boolean {
  return project.type !== "favorites";
}
