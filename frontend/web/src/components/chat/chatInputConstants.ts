import { Brain, Zap, Settings, type LucideIcon } from "lucide-react";
import { Permission, type FileCategory } from "../../types";

export const FILE_CATEGORY_PERMISSIONS: Record<FileCategory, Permission> = {
  image: Permission.FILE_UPLOAD_IMAGE,
  video: Permission.FILE_UPLOAD_VIDEO,
  audio: Permission.FILE_UPLOAD_AUDIO,
  document: Permission.FILE_UPLOAD_DOCUMENT,
};

export const ICON_MAP: Record<string, LucideIcon> = {
  Brain,
  Zap,
  Settings,
};

/** When pasted text exceeds this length, auto-convert to a .txt file upload. */
export const PASTE_TEXT_THRESHOLD = 3000;

export const THINKING_LEVEL_COLOR: Record<
  string,
  { border: string; bg: string; text: string }
> = {
  auto: {
    border: "transparent",
    bg: "transparent",
    text: "var(--theme-text-secondary)",
  },
  low: {
    border: "var(--theme-info-ring)",
    bg: "var(--theme-info-soft)",
    text: "var(--theme-info-text)",
  },
  medium: {
    border: "var(--theme-warning-ring)",
    bg: "var(--theme-warning-soft)",
    text: "var(--theme-warning-text)",
  },
  high: {
    border: "var(--theme-danger-ring)",
    bg: "var(--theme-danger-soft)",
    text: "var(--theme-danger-text)",
  },
};
