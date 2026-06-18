import { useState, useCallback } from "react";
import { projectApi } from "../services/api";
import type { Project } from "../types";

interface UseProjectManagerReturn {
  projects: Project[];
  loadProjects: () => Promise<void>;
}

export function useProjectManager(): UseProjectManagerReturn {
  const [projects, setProjects] = useState<Project[]>([]);

  const loadProjects = useCallback(async () => {
    try {
      const projectList = await projectApi.list();
      setProjects(projectList);
    } catch (err) {
      console.error("Failed to load projects:", err);
    }
  }, []);

  return {
    projects,
    loadProjects,
  };
}
