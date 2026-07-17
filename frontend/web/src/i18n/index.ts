import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zh from "./locales/zh.json";
import ja from "./locales/ja.json";
import ko from "./locales/ko.json";
import ru from "./locales/ru.json";

/** The product UI is Chinese-only; other locale bundles remain compatibility assets. */
export const PRODUCT_LANGUAGE = "zh";

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
    ja: { translation: ja },
    ko: { translation: ko },
    ru: { translation: ru },
  },
  lng: PRODUCT_LANGUAGE,
  fallbackLng: PRODUCT_LANGUAGE,
  interpolation: {
    escapeValue: false,
  },
});

export default i18n;
