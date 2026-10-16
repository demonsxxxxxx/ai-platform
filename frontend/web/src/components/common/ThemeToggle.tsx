import { Button } from "@lobehub/ui/base-ui";
import { Sun, Moon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useTheme } from "../../contexts/ThemeContext";

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const { t } = useTranslation();

  return (
    <Button type="text"
      onClick={toggleTheme}
      className={className}
      aria-label={theme === "light" ? t("theme.switchToDark") : t("theme.switchToLight")}
      icon={theme === "light" ? <Moon size={20} /> : <Sun size={20} className="text-amber-400" />}
      title={
        theme === "light" ? t("theme.switchToDark") : t("theme.switchToLight")
      }
    />
  );
}
