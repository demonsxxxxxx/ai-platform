import type { ReactNode } from "react";
import ConfigProvider from "@lobehub/ui/es/ConfigProvider/index";
import LobeThemeProvider from "@lobehub/ui/es/ThemeProvider/index";
import { en, zhCn } from "@lobehub/ui/i18n";
import { motion } from "motion/react";
import { useTranslation } from "react-i18next";
import { useTheme } from "./ThemeContext";

/** Connect library styling and portals to the application's existing preferences. */
export function LobeUiProvider({ children }: { children: ReactNode }) {
  const { theme } = useTheme();
  const { i18n } = useTranslation();
  const chinese = i18n.language.startsWith("zh");

  return (
    <ConfigProvider motion={motion} locale={chinese ? "zh-CN" : "en-US"} resources={chinese ? zhCn : en}>
      <LobeThemeProvider
        appearance={theme}
        enableCustomFonts={false}
        enableGlobalStyle={false}
        theme={{ token: { borderRadius: 8, fontFamily: "var(--font-ui)" } }}
      >
        {children}
      </LobeThemeProvider>
    </ConfigProvider>
  );
}
