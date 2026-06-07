import { useRef, useCallback, useState } from "react";
import { ArrowUp, Square, Lock, X, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { FeatureMenu, type FeaturePanel } from "../selectors/FeatureMenu";
import {
  PersonaAvatarIcon,
  PersonaAvatarImage,
} from "../persona/PersonaAvatarIcon";
import { isEmojiAvatar, getEmojiAvatarUrl } from "../persona/personaAvatar";
import type { FileCategory } from "../../types";
import type { UploadLimits } from "../../hooks/useFileUpload";

export interface ChatInputToolbarProps {
  activePanel: FeaturePanel;
  onActivePanelChange: (panel: FeaturePanel) => void;
  canSend: boolean;
  isLoading: boolean;
  canSubmit: boolean;
  hasUploadingAttachment: boolean;
  enabledToolsCount: number;
  totalToolsCount: number;
  enabledSkillsCount: number;
  totalSkillsCount: number;
  hasPersonaSelector: boolean;
  personaName?: string | null;
  hasAgentSelector: boolean;
  agentName?: string;
  hasThinkingOption: boolean;
  thinkingLabel?: string;
  thinkingLevel?: string;
  uploadCategories: FileCategory[];
  uploadLimits: UploadLimits | null;
  uploadFiles: (files: FileList | File[], category?: FileCategory) => void;
  selectedPersonaName?: string | null;
  personaAvatar: { avatar?: string; primaryTag: string } | null;
  onClearPersonaPreset?: () => void;
  onStopClick: () => void;
  onNoPermissionClick: () => void;
}

const FILE_CATEGORY_ACCEPT: Record<FileCategory, string> = {
  image: "image/*",
  video: "video/*",
  audio: "audio/*",
  document: ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv",
};

export function ChatInputToolbar({
  activePanel,
  onActivePanelChange,
  canSend,
  isLoading,
  canSubmit,
  hasUploadingAttachment,
  enabledToolsCount,
  totalToolsCount,
  enabledSkillsCount,
  totalSkillsCount,
  hasPersonaSelector,
  personaName,
  hasAgentSelector,
  agentName,
  hasThinkingOption,
  thinkingLabel,
  thinkingLevel,
  uploadCategories,
  uploadLimits,
  uploadFiles,
  selectedPersonaName,
  personaAvatar,
  onClearPersonaPreset,
  onStopClick,
  onNoPermissionClick,
}: ChatInputToolbarProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFileCategory, setSelectedFileCategory] =
    useState<FileCategory | null>(null);

  const handleFileCategorySelect = useCallback((category: FileCategory) => {
    setSelectedFileCategory(category);
    if (fileInputRef.current) {
      fileInputRef.current.accept = FILE_CATEGORY_ACCEPT[category];
      fileInputRef.current.click();
    }
  }, []);

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      uploadFiles(files, selectedFileCategory || undefined);
      e.target.value = "";
    },
    [uploadFiles, selectedFileCategory],
  );

  return (
    <div className="flex justify-between flex-nowrap pt-3 pb-3 px-2 mx-0.5 max-w-full">
      <div className="flex items-center gap-1 sm:gap-2 self-end flex-1 min-w-0 overflow-x-auto no-scrollbar">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileInputChange}
        />
        <FeatureMenu
          activePanel={activePanel}
          onOpen={onActivePanelChange}
          enabledToolsCount={enabledToolsCount}
          totalToolsCount={totalToolsCount}
          enabledSkillsCount={enabledSkillsCount}
          totalSkillsCount={totalSkillsCount}
          hasPersonaSelector={hasPersonaSelector}
          personaName={personaName}
          hasAgentSelector={hasAgentSelector}
          agentName={agentName}
          hasThinkingOption={hasThinkingOption}
          uploadCategories={uploadCategories}
          uploadLimits={uploadLimits}
          onFileCategorySelect={handleFileCategorySelect}
          thinkingLabel={thinkingLabel}
          thinkingLevel={thinkingLevel}
        />
        {selectedPersonaName && (
          <button
            type="button"
            className="chat-tool-btn group shrink min-w-0"
            onClick={() => onActivePanelChange("persona")}
            title={selectedPersonaName}
          >
            <div className="flex flex-row items-center gap-1.5 min-w-0">
              <span className="relative w-[18px] h-[18px] shrink-0 inline-flex items-center justify-center">
                {personaAvatar?.avatar &&
                (personaAvatar.avatar.startsWith("http") ||
                  personaAvatar.avatar.startsWith("/") ||
                  isEmojiAvatar(personaAvatar.avatar)) ? (
                  <PersonaAvatarImage
                    avatar={
                      isEmojiAvatar(personaAvatar.avatar)
                        ? getEmojiAvatarUrl(personaAvatar.avatar)
                        : personaAvatar.avatar
                    }
                    alt=""
                    className="w-[18px] h-[18px] rounded-full object-cover group-hover:opacity-0 transition-opacity"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = "none";
                    }}
                  />
                ) : (
                  <PersonaAvatarIcon
                    avatar={personaAvatar?.avatar}
                    primaryTag={personaAvatar?.primaryTag ?? ""}
                    size={18}
                    className="transition-transform duration-200 group-hover:opacity-0"
                  />
                )}
                {onClearPersonaPreset && (
                  <X
                    size={18}
                    className="absolute inset-0 m-auto opacity-0 group-hover:opacity-100 transition-opacity"
                    onClick={(e) => {
                      e.stopPropagation();
                      onClearPersonaPreset();
                    }}
                  />
                )}
              </span>
              <span className="max-w-40 truncate text-sm font-semibold text-blue-600 dark:text-blue-400">
                {selectedPersonaName}
              </span>
              <ChevronDown size={14} className="opacity-50 shrink-0" />
            </div>
          </button>
        )}
      </div>

      <div className="self-end flex space-x-1.5 flex-shrink-0">
        {!canSend ? (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onNoPermissionClick();
            }}
            className="flex items-center justify-center rounded-full p-2 cursor-pointer transition-all duration-200 hover:scale-105"
            style={{
              backgroundColor: "var(--theme-primary-light)",
              color: "var(--theme-text-secondary)",
            }}
            title={t("chat.noPermission")}
          >
            <Lock size={18} />
          </button>
        ) : isLoading ? (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onStopClick();
            }}
            className="chat-tool-btn-active flex items-center justify-center rounded-full p-2 transition-all duration-300 hover:scale-105 active:scale-95"
            style={{
              borderColor: "color-mix(in srgb, #fbbf24 40%, transparent)",
              background: "color-mix(in srgb, #fbbf24 10%, transparent)",
              color: "#fbbf24",
            }}
            title={t("chat.stop")}
          >
            <Square size={16} fill="currentColor" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSubmit}
            className={`flex items-center justify-center rounded-full p-2 transition-all duration-300 ${
              canSubmit ? "hover:scale-105 active:scale-95" : ""
            }`}
            style={{
              backgroundColor: "transparent",
              border: canSubmit
                ? "1px solid color-mix(in srgb, var(--theme-primary) 40%, transparent)"
                : "1px solid var(--theme-border)",
              color: canSubmit
                ? "var(--theme-primary)"
                : "var(--theme-text-secondary)",
            }}
            title={
              hasUploadingAttachment
                ? t("chat.waitingForUpload", "请等待文件上传完成")
                : t("chat.send")
            }
          >
            <ArrowUp size={18} />
          </button>
        )}
      </div>
    </div>
  );
}
