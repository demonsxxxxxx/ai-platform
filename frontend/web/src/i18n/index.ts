import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import zh from "./locales/zh.json";

/** The product UI and its only shipped translation catalog are Chinese. */
export const PRODUCT_LANGUAGE = "zh";

i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
  },
  lng: PRODUCT_LANGUAGE,
  fallbackLng: PRODUCT_LANGUAGE,
  interpolation: {
    escapeValue: false,
  },
});

export default i18n;
