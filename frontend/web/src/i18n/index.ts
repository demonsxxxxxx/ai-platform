import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zh from "./locales/zh.json";
import ja from "./locales/ja.json";
import ko from "./locales/ko.json";
import ru from "./locales/ru.json";

const SUPPORTED_LANGUAGES = ["en", "zh", "ja", "ko", "ru"];

const detectLanguage = (): string => {
  // Check if running in browser environment
  if (typeof window === "undefined") {
    return "zh";
  }

  // Explicit user or backend-synchronized preference wins.
  const saved = localStorage.getItem("language");
  if (saved && SUPPORTED_LANGUAGES.includes(saved)) {
    return saved;
  }

  // No preference defaults to Chinese.
  return "zh";
};

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
    ja: { translation: ja },
    ko: { translation: ko },
    ru: { translation: ru },
  },
  lng: detectLanguage(),
  fallbackLng: "zh",
  interpolation: {
    escapeValue: false,
  },
});

export default i18n;
