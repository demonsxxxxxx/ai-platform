import { useRef, useCallback, useEffect } from "react";
import {
  ArrowUp,
  Boxes,
  ChevronDown,
  Lock,
  Paperclip,
  Square,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { FeatureMenu, type FeaturePanel } from "../selectors/FeatureMenu";
import { AgentOptionButton } from "./AgentOptionButton";
import type { ModelOption } from "../../services/api/modelPublic";
import type { AgentOption, FileCategory } from "../../types";

export interface ChatInputToolbarProps {
  activePanel: FeaturePanel;
  onActivePanelChange: (panel: FeaturePanel) => void;
  canSend: boolean;
  isLoading: boolean;
  canStop: boolean;
  canSubmit: boolean;
  hasUploadingAttachment: boolean;
  enabledToolsCount: number;
  totalToolsCount: number;
  enabledSkillsCount: number;
  totalSkillsCount: number;
  availableModels: ModelOption[];
  currentModelId?: string;
  onSelectModel?: (modelId: string, modelValue: string) => void;
  showModelSelector?: boolean;
  agentOptions?: Record<string, AgentOption>;
  agentOptionValues?: Record<string, boolean | string | number>;
  onToggleAgentOption?: (key: string, value: boolean | string | number) => void;
  uploadCategories: FileCategory[];
  uploadFiles: (files: FileList | File[], category?: FileCategory) => void;
  onFileCommandReady?: (openFileCommand: () => void) => void;
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
  canStop,
  canSubmit,
  hasUploadingAttachment,
  enabledToolsCount,
  totalToolsCount,
  enabledSkillsCount,
  totalSkillsCount,
  availableModels,
  currentModelId,
  onSelectModel,
  showModelSelector = false,
  agentOptions,
  agentOptionValues,
  onToggleAgentOption,
  uploadCategories,
  uploadFiles,
  onFileCommandReady,
  onStopClick,
  onNoPermissionClick,
}: ChatInputToolbarProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const currentModel = availableModels.find(
    (model) => model.id === currentModelId,
  );
  const attachmentAccept = uploadCategories
    .map((category) => FILE_CATEGORY_ACCEPT[category])
    .join(",");

  const openFilePicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  useEffect(() => {
    if (!onFileCommandReady) return;
    onFileCommandReady(openFilePicker);
  }, [onFileCommandReady, openFilePicker]);

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      uploadFiles(files);
      e.target.value = "";
    },
    [uploadFiles],
  );

  return (
    <div className="flex min-h-11 max-w-full items-end justify-between gap-2 px-3 pb-2 pt-1">
      <div className="min-w-0 flex-1">
        {showModelSelector && onSelectModel && availableModels.length > 0 ? (
          <button
            type="button"
            data-composer-model-trigger
            onClick={() =>
              onActivePanelChange(activePanel === "model" ? null : "model")
            }
            className={`flex h-9 w-full min-w-0 max-w-[min(20rem,55vw)] items-center gap-2 rounded-lg border px-2.5 text-sm font-medium transition-colors sm:w-fit ${
              activePanel === "model"
                ? "border-[var(--theme-primary)] bg-[var(--theme-primary-light)] text-[var(--theme-primary)]"
                : "border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] hover:border-[var(--theme-ring)]"
            }`}
            aria-label={t(
              "composerCommand.modelSelector.title",
              "选择模型",
            )}
            aria-expanded={activePanel === "model"}
          >
            <Boxes size={17} className="shrink-0" />
            <span className="truncate">
              {currentModel?.label ??
                t("composerCommand.modelSelector.title", "选择模型")}
            </span>
            <ChevronDown
              size={15}
              className={`shrink-0 text-[var(--theme-text-secondary)] transition-transform ${
                activePanel === "model" ? "rotate-180" : ""
              }`}
            />
          </button>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={attachmentAccept}
          className="hidden"
          onChange={handleFileInputChange}
        />
        <FeatureMenu
          activePanel={activePanel}
          onOpen={onActivePanelChange}
          triggerLabel={t("chat.commandTrigger")}
          enabledToolsCount={enabledToolsCount}
          totalToolsCount={totalToolsCount}
          enabledSkillsCount={enabledSkillsCount}
          totalSkillsCount={totalSkillsCount}
        />
        {agentOptions?.enable_thinking?.options?.length && onToggleAgentOption ? (
          <AgentOptionButton
            optionKey="enable_thinking"
            option={agentOptions.enable_thinking}
            value={
              agentOptionValues?.enable_thinking ??
              agentOptions.enable_thinking.default
            }
            onChange={(value) => onToggleAgentOption("enable_thinking", value)}
          />
        ) : null}
        {uploadCategories.length > 0 ? (
          <button
            type="button"
            className="chat-tool-btn"
            onClick={openFilePicker}
            aria-label={t("chat.attachFile", "添加附件")}
            title={t("chat.attachFile", "添加附件")}
          >
            <Paperclip size={19} />
          </button>
        ) : null}

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
          canStop ? (
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
              type="button"
              disabled
              aria-busy="true"
              className="chat-tool-btn flex items-center justify-center rounded-full p-2 opacity-60"
              title={t("chat.generating", "生成中")}
            >
              <Square size={16} />
            </button>
          )
        ) : (
          <button
            type="submit"
            disabled={!canSubmit}
            className={`flex items-center justify-center rounded-full p-2 transition-all duration-300 ${
              canSubmit ? "hover:scale-105 active:scale-95" : ""
            }`}
            style={{
              backgroundColor: canSubmit
                ? "var(--theme-send-bg)"
                : "transparent",
              border: canSubmit
                ? "1px solid var(--theme-send-bg)"
                : "1px solid var(--theme-border)",
              color: canSubmit
                ? "var(--theme-send-fg)"
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
