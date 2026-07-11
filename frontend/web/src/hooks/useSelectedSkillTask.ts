import { useCallback, useEffect, useReducer } from "react";

import type {
  MessageAttachment,
  PublicSkillResponse,
  SelectedSkillRequest,
} from "../types";

export const SELECTED_SKILL_RECOVERABLE_CODES = [
  "skill_selection_stale",
  "capability_not_authorized",
  "file_required_for_skill",
] as const;

export type SelectedSkillRecoverableCode =
  (typeof SELECTED_SKILL_RECOVERABLE_CODES)[number];

export type SelectedSkillTaskStatus =
  | "idle"
  | "confirmed"
  | "stale"
  | "denied"
  | "file_required";

export interface SelectedSkillTaskState {
  selectedSkill: PublicSkillResponse | null;
  status: SelectedSkillTaskStatus;
  recoveryCode: SelectedSkillRecoverableCode | null;
  requiresReconfirmation: boolean;
}

export type SelectedSkillTaskAction =
  | { type: "select"; skill: PublicSkillResponse }
  | { type: "clear" }
  | { type: "files_ready" }
  | { type: "recoverable_error"; code: SelectedSkillRecoverableCode }
  | { type: "refresh_complete"; skills: PublicSkillResponse[] };

export interface UseSelectedSkillTaskOptions {
  skills: PublicSkillResponse[];
  skillsLoading: boolean;
  refreshSkills: () => Promise<unknown>;
}

export function createSelectedSkillTaskState(): SelectedSkillTaskState {
  return {
    selectedSkill: null,
    status: "idle",
    recoveryCode: null,
    requiresReconfirmation: false,
  };
}

export function selectedSkillTaskReducer(
  state: SelectedSkillTaskState,
  action: SelectedSkillTaskAction,
): SelectedSkillTaskState {
  switch (action.type) {
    case "select":
      return {
        selectedSkill: action.skill,
        status: "confirmed",
        recoveryCode: null,
        requiresReconfirmation: false,
      };
    case "clear":
      return createSelectedSkillTaskState();
    case "files_ready":
      return state.status === "file_required" && state.selectedSkill
        ? {
            ...state,
            status: "confirmed",
            recoveryCode: null,
          }
        : state;
    case "recoverable_error":
      if (action.code === "skill_selection_stale") {
        return {
          ...state,
          status: "stale",
          recoveryCode: action.code,
          requiresReconfirmation: true,
        };
      }
      if (action.code === "capability_not_authorized") {
        return {
          selectedSkill: null,
          status: "denied",
          recoveryCode: action.code,
          requiresReconfirmation: true,
        };
      }
      return {
        ...state,
        status: "file_required",
        recoveryCode: action.code,
      };
    case "refresh_complete": {
      if (!state.selectedSkill) return state;
      const current = action.skills.find(
        (skill) => skill.name === state.selectedSkill?.name,
      );
      if (!current) {
        return {
          selectedSkill: null,
          status: "denied",
          recoveryCode: "capability_not_authorized",
          requiresReconfirmation: true,
        };
      }
      if (
        state.status === "stale" ||
        state.requiresReconfirmation ||
        current.expected_version !== state.selectedSkill.expected_version
      ) {
        return {
          ...state,
          status: "stale",
          recoveryCode: "skill_selection_stale",
          requiresReconfirmation: true,
        };
      }
      return state;
    }
    default:
      return state;
  }
}

export function getSelectedSkillPreflightError(
  state: SelectedSkillTaskState,
  attachments: Array<Pick<MessageAttachment, "id" | "isUploading">>,
): SelectedSkillRecoverableCode | null {
  if (state.requiresReconfirmation) {
    return state.recoveryCode ?? "capability_not_authorized";
  }
  if (
    state.selectedSkill?.requires_file &&
    !attachments.some((attachment) => attachment.id && !attachment.isUploading)
  ) {
    return "file_required_for_skill";
  }
  return null;
}

export function toSelectedSkillRequest(
  state: SelectedSkillTaskState,
): SelectedSkillRequest | null {
  if (state.status !== "confirmed" || !state.selectedSkill) return null;
  return {
    skill_id: state.selectedSkill.name,
    expected_version: state.selectedSkill.expected_version,
  };
}

/** Owns one task-scoped Skill selection and its explicit refresh/reconfirm flow. */
export function useSelectedSkillTask({
  skills,
  skillsLoading,
  refreshSkills,
}: UseSelectedSkillTaskOptions) {
  const [state, dispatch] = useReducer(
    selectedSkillTaskReducer,
    undefined,
    createSelectedSkillTaskState,
  );

  useEffect(() => {
    if (!skillsLoading) {
      dispatch({ type: "refresh_complete", skills });
    }
  }, [skills, skillsLoading]);

  const selectSkill = useCallback((skill: PublicSkillResponse) => {
    dispatch({ type: "select", skill });
  }, []);

  const clearSelection = useCallback(() => {
    dispatch({ type: "clear" });
  }, []);

  const recover = useCallback(
    async (code: SelectedSkillRecoverableCode) => {
      dispatch({ type: "recoverable_error", code });
      await refreshSkills();
    },
    [refreshSkills],
  );

  const markFilesReady = useCallback(() => {
    dispatch({ type: "files_ready" });
  }, []);

  return {
    state,
    selectedSkill: state.selectedSkill,
    selectSkill,
    clearSelection,
    recover,
    markFilesReady,
  };
}
