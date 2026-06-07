import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { APP_NAME } from "../constants";

const DEFAULT_DESCRIPTION =
  "A pluggable, multi-tenant AI conversation system. Skills + MCP dual-engine driven, modular by design.";

const DEFAULT_OG_TYPE = "website";

const CRAWLER_ROBOTS_META_NAMES = [
  "robots",
  "googlebot",
  "bingbot",
  "Baiduspider",
  "360Spider",
  "Sogou web spider",
  "YisouSpider",
  "Bytespider",
];

function setMetaContent(selector: string, content: string) {
  const el = document.querySelector(selector);
  if (el) el.setAttribute("content", content);
}

function setOrCreateMeta(
  attr: string,
  attrValue: string,
  content: string,
  tagName: string = "meta",
) {
  let el = document.querySelector(
    `${tagName}[${attr}="${attrValue}"]`,
  ) as HTMLElement | null;
  if (!el) {
    el = document.createElement(tagName) as HTMLElement;
    el.setAttribute(attr, attrValue);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setOrCreateLink(rel: string, href: string) {
  let el = document.querySelector(
    `link[rel="${rel}"]`,
  ) as HTMLLinkElement | null;
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}

function setRobots(noindex: boolean, nofollow: boolean) {
  const content = `${noindex ? "noindex" : "index"}, ${
    nofollow ? "nofollow" : "follow"
  }`;
  for (const crawler of CRAWLER_ROBOTS_META_NAMES) {
    setOrCreateMeta("name", crawler, content);
  }
}

export interface SEOConfig {
  title: string;
  description?: string;
  path?: string;
  ogType?: string;
  noindex?: boolean;
  omitSuffix?: boolean;
}

export function useSEO(config: SEOConfig) {
  const { t } = useTranslation();

  useEffect(() => {
    const {
      title,
      description,
      path,
      ogType = DEFAULT_OG_TYPE,
      noindex = false,
      omitSuffix = false,
    } = config;

    const translatedTitle = title ? t(title) : APP_NAME;
    const fullTitle = omitSuffix
      ? translatedTitle
      : `${translatedTitle} - ${APP_NAME}`;

    document.title = fullTitle;

    const desc = description ? t(description) : DEFAULT_DESCRIPTION;

    setMetaContent('meta[name="description"]', desc);
    setOrCreateMeta("property", "og:title", fullTitle);
    setOrCreateMeta("property", "og:description", desc);
    setOrCreateMeta("property", "og:type", ogType);
    setOrCreateMeta("name", "twitter:title", fullTitle);
    setMetaContent('meta[name="twitter:description"]', desc);

    if (path) {
      const url = `${window.location.origin}${path}`;
      setOrCreateMeta("property", "og:url", url);
      setOrCreateLink("canonical", url);
    }

    if (noindex) {
      setRobots(true, true);
    }

    return () => {
      document.title = APP_NAME;
      setMetaContent('meta[name="description"]', DEFAULT_DESCRIPTION);
      setOrCreateMeta("property", "og:title", APP_NAME);
      setOrCreateMeta("property", "og:description", DEFAULT_DESCRIPTION);
      setOrCreateMeta("property", "og:type", DEFAULT_OG_TYPE);
      setOrCreateMeta("name", "twitter:title", APP_NAME);
      setMetaContent('meta[name="twitter:description"]', DEFAULT_DESCRIPTION);
      for (const crawler of CRAWLER_ROBOTS_META_NAMES) {
        setMetaContent(`meta[name="${crawler}"]`, "index, follow");
      }
    };
  });
}

export function usePageTitle(
  title: string,
  suffix: string = APP_NAME,
  options?: { isI18nKey?: boolean; description?: string },
) {
  const { t } = useTranslation();
  const isI18nKey = options?.isI18nKey ?? true;
  const description = options?.description;

  useEffect(() => {
    const translatedTitle = isI18nKey && title ? t(title) : title;
    const translatedSuffix = isI18nKey ? t("appName") || suffix : suffix;

    const fullTitle = translatedTitle
      ? `${translatedTitle} - ${translatedSuffix}`
      : translatedSuffix;
    document.title = fullTitle;

    const desc = description && (isI18nKey ? t(description) : description);
    if (desc) {
      setMetaContent('meta[name="description"]', desc);
      setMetaContent('meta[property="og:description"]', desc);
      setMetaContent('meta[name="twitter:description"]', desc);
    }

    return () => {
      document.title = isI18nKey ? t("appName") || suffix : suffix;
      setMetaContent('meta[name="description"]', DEFAULT_DESCRIPTION);
      setMetaContent('meta[property="og:description"]', DEFAULT_DESCRIPTION);
      setMetaContent('meta[name="twitter:description"]', DEFAULT_DESCRIPTION);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, suffix, isI18nKey, description]);
}
