import { Button, DropdownMenu } from "@lobehub/ui/base-ui";
import { memo, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Wrench, Sparkles, Plus } from "lucide-react";

export type FeaturePanel =
  | "tools"
  | "skills"
  | "model"
  | "file"
  | "thinking"
  | null;

interface FeatureMenuProps {
  activePanel: FeaturePanel;
  onOpen: (panel: FeaturePanel) => void;
  triggerLabel?: string;
  enabledToolsCount: number;
  totalToolsCount: number;
  enabledSkillsCount: number;
  totalSkillsCount: number;
}

export const FeatureMenu = memo(function FeatureMenu({
  activePanel,
  onOpen,
  triggerLabel,
  enabledToolsCount,
  totalToolsCount,
  enabledSkillsCount,
  totalSkillsCount,
}: FeatureMenuProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  useEffect(() => { if (activePanel) setIsOpen(false); }, [activePanel]);
  if (totalToolsCount === 0 && totalSkillsCount === 0) return null;
  const label = triggerLabel ?? t("chat.features", "功能");
  return (
    <DropdownMenu open={isOpen} onOpenChange={setIsOpen} placement="topLeft" items={[
      { type: "group", key: "enhance", label: t("featureMenu.enhance", "增强"), children: [
        totalToolsCount > 0 ? { key: "tools", icon: Wrench, label: `${t("featureMenu.mcpTools")} ${enabledToolsCount}/${totalToolsCount}`, onClick: () => onOpen("tools") } : null,
        totalSkillsCount > 0 ? { key: "skills", icon: Sparkles, label: `${t("featureMenu.skillsMarketplace")} ${enabledSkillsCount}/${totalSkillsCount}`, onClick: () => onOpen("skills") } : null,
      ] },
    ]}>
      <Button type="text" size="large" aria-label={label} title={label} icon={<Plus size={18} />} />
    </DropdownMenu>
  );
});
