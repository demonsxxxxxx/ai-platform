import { ComposerToolbar, ComposerActions, ComposerSubmit } from "./assistant-ui/elements/composer";
import { Button } from "@lobehub/ui/base-ui";
import { useRef, useCallback, useEffect } from "react";
import {
  Boxes,
  ChevronDown,
  Lock,
  CircleAlert,
  Paperclip,
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
  unavailableReason?: string;
  isLoading: boolean;
  canStop: boolean;
  canSubmit: boolean;
  modelUnavailableReason?: string;
  hasUploadingAttachment: boolean;
  enabledToolsCount: number;
  totalToolsCount: number;
  enabledSkillsCount: number;
  totalSkillsCount: number;
  availableModels: ModelOption[];
  currentModelId?: string;
  modelSelectionMode?: "auto" | "explicit";
  onSelectModel?: (modelId: string, modelValue: string, mode?: "auto" | "explicit") => void;
  showModelSelector?: boolean;
  agentOptions?: Record<string, AgentOption>;
  agentOptionValues?: Record<string, boolean | string | number>;
  onToggleAgentOption?: (key: string, value: boolean | string | number) => void;
  uploadCategories: FileCategory[];
  uploadFiles: (files: FileList | File[], category?: FileCategory) => void;
  onFileCommandReady?: (openFileCommand: () => void) => void;
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
  unavailableReason,
  isLoading,
  canStop,
  canSubmit,
  modelUnavailableReason,
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
    <ComposerToolbar className="flex-wrap">
      <div className="flex min-w-0 basis-full flex-wrap items-center gap-2 sm:basis-auto sm:flex-1">
        {showModelSelector && onSelectModel ? (
          <Button type={activePanel === "model" ? "primary" : "default"} size="large"
            htmlType="button"
            data-composer-model-trigger
            onClick={() =>
              onActivePanelChange(activePanel === "model" ? null : "model")
            }
            className="min-w-0"
            style={{ maxWidth: "min(20rem, 100%)" }}
            icon={<Boxes size={17} />}
            aria-label={t(
              "composerCommand.modelSelector.title",
              "选择模型",
            )}
            aria-expanded={activePanel === "model"}
          >
            <span className="truncate">
              {currentModel?.label ?? (modelUnavailableReason ? "暂无可用模型" : t("composerCommand.modelSelector.title", "选择模型"))}
            </span>
            <ChevronDown
              size={15}
              className={`shrink-0 text-[var(--theme-text-secondary)] transition-transform ${
                activePanel === "model" ? "rotate-180" : ""
              }`}
            />
          </Button>
        ) : null}
        {modelUnavailableReason ? <div role="status" data-composer-model-status className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-[var(--theme-danger-text)]">
          <CircleAlert aria-hidden="true" size={14} className="shrink-0" />
          <span>{modelUnavailableReason}</span>
        </div> : null}
      </div>

      <ComposerActions className="ml-auto">
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
          <Button type="text"
            htmlType="button"
            size="large" icon={<Paperclip size={19} />}
            onClick={openFilePicker}
            aria-label={t("chat.attachFile", "添加附件")}
            title={t("chat.attachFile", "添加附件")}
          />
        ) : null}

        {!canSend ? (
          <Button type="text" shape="circle" size="large" htmlType="button"
            onClick={(event) => { event.preventDefault(); event.stopPropagation(); onNoPermissionClick(); }}
            aria-label={unavailableReason ?? t("chat.noPermission")} title={unavailableReason ?? t("chat.noPermission")} icon={<Lock size={18} />}
            disabled={Boolean(unavailableReason)}
          />
        ) : (
          <ComposerSubmit running={isLoading} canStop={canStop} canSubmit={canSubmit} uploading={hasUploadingAttachment} />
        )}
      </ComposerActions>
    </ComposerToolbar>
  );
}
