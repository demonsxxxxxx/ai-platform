import { Sparkles, Copy, Tag, FileText, Zap, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { EditorSidebar } from "../common/EditorSidebar";
import { PersonaAvatarIcon, PersonaAvatarImage } from "./PersonaAvatarIcon";
import {
  isPersonaImageAvatar,
  isEmojiAvatar,
  getEmojiAvatarUrl,
} from "./personaAvatar";
import type { PersonaPreset } from "../../types";

interface PersonaPreviewSidebarProps {
  preset: PersonaPreset;
  isSelected: boolean;
  isMutating: boolean;
  isUsingPreset: boolean;
  onClose: () => void;
  onUsePreset: (preset: PersonaPreset) => Promise<void>;
  onCopyPreset: (preset: PersonaPreset) => void;
}

export function PersonaPreviewSidebar({
  preset,
  isSelected,
  isMutating,
  isUsingPreset,
  onClose,
  onUsePreset,
  onCopyPreset,
}: PersonaPreviewSidebarProps) {
  const { t } = useTranslation();
  const primaryTag = preset.tags[0];

  return (
    <EditorSidebar
      open={true}
      onClose={onClose}
      title={preset.name}
      subtitle={`${
        preset.scope === "global"
          ? t("personaPresets.official", "官方")
          : t("personaPresets.mine", "我的")
      }${
        preset.usage_count > 0
          ? ` · ${preset.usage_count}${t(
              "personaPresets.usageCount",
              "次使用",
            )}`
          : ""
      }`}
      icon={
        <div className="flex h-7 w-7 items-center justify-center overflow-hidden rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]">
          {isPersonaImageAvatar(preset.avatar) ||
          isEmojiAvatar(preset.avatar) ? (
            <PersonaAvatarImage
              avatar={
                isEmojiAvatar(preset.avatar)
                  ? getEmojiAvatarUrl(preset.avatar)
                  : preset.avatar
              }
              alt=""
              className="h-5 w-5 rounded object-cover"
            />
          ) : (
            <PersonaAvatarIcon
              avatar={preset.avatar}
              primaryTag={primaryTag}
              size={14}
              className="text-[var(--theme-primary)]"
            />
          )}
        </div>
      }
      footer={
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={isMutating || isSelected || isUsingPreset}
            aria-busy={isUsingPreset}
            onClick={() => onUsePreset(preset)}
            className={`pps-card__action flex-1 justify-center py-2.5 text-xs font-semibold ${
              isSelected
                ? "pps-card__action--active"
                : "pps-card__action--primary"
            } ${isUsingPreset ? "pps-card__action--loading" : ""}`}
          >
            {isUsingPreset ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Sparkles size={14} />
            )}
            {isUsingPreset
              ? t("personaPresets.applying", "使用中...")
              : isSelected
                ? t("personaPresets.using", "使用中")
                : t("personaPresets.use", "使用")}
          </button>
          {preset.scope === "global" && (
            <button
              type="button"
              disabled={isMutating}
              onClick={() => onCopyPreset(preset)}
              className="pps-card__action pps-card__action--ghost flex-1 justify-center py-2.5 text-xs font-semibold"
            >
              <Copy size={14} />
              {t("personaPresets.copy", "复制")}
            </button>
          )}
        </div>
      }
    >
      <div className="es-form">
        <div className="enterprise-subtle-panel flex items-start gap-3">
          <div className="pps-card__avatar !h-12 !w-12 !rounded-lg">
            {isPersonaImageAvatar(preset.avatar) ||
            isEmojiAvatar(preset.avatar) ? (
              <PersonaAvatarImage
                avatar={
                  isEmojiAvatar(preset.avatar)
                    ? getEmojiAvatarUrl(preset.avatar)
                    : preset.avatar
                }
                alt=""
                className="pps-card__avatar-img"
              />
            ) : (
              <PersonaAvatarIcon
                avatar={preset.avatar}
                primaryTag={primaryTag}
                size={24}
                className="pps-card__avatar-icon"
              />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-[var(--theme-text)]">
              {preset.name}
            </p>
            <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
              {preset.scope === "global"
                ? t("personaPresets.official", "官方")
                : t("personaPresets.mine", "我的")}
              {preset.usage_count > 0
                ? ` · ${preset.usage_count}${t(
                    "personaPresets.usageCount",
                    "次使用",
                  )}`
                : ""}
            </p>
          </div>
        </div>

        <div>
          {preset.description ? (
            <p
              className="text-[13px] leading-relaxed"
              style={{ color: "var(--theme-text-secondary)" }}
            >
              {preset.description}
            </p>
          ) : (
            <p
              className="text-[13px] italic"
              style={{
                color:
                  "var(--theme-text-tertiary, var(--theme-text-secondary))",
              }}
            >
              {t("personaPresets.descriptionPlaceholder", "暂无简介")}
            </p>
          )}
        </div>

        {/* Tags section */}
        {preset.tags.length > 0 && (
          <div className="es-section">
            <div className="es-section-title">
              <Tag />
              {t("personaPresets.tags", "标签")}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {preset.tags.map((tag) => (
                <span key={tag} className="es-chip">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* System Prompt section */}
        <div className="es-section">
          <div className="es-section-title">
            <FileText />
            {t("personaPresets.systemPrompt", "系统提示词")}
          </div>
          <div
            className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-3 font-mono text-[12px] leading-[1.7]"
            style={{ color: "var(--theme-text)" }}
          >
            {preset.system_prompt}
          </div>
        </div>

        {/* Skills section */}
        {preset.skill_names.length > 0 && (
          <div className="es-section">
            <div className="es-section-title">
              <Zap />
              {t("personaPresets.skills", "技能")}
              <span className="ml-auto font-mono text-[10px] opacity-60">
                {preset.skill_names.length}
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {preset.skill_names.map((name) => (
                <span key={name} className="es-chip">
                  <Sparkles size={10} className="opacity-50" />
                  {name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </EditorSidebar>
  );
}
