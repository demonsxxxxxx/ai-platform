import DOMPurify from "dompurify";

const SAFE_SRC_DOC_BASE = '<base href="about:srcdoc" />';
const HTML_PREVIEW_CSP =
  "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'";
const SAFE_IMAGE_DATA_URL =
  /^data:image\/(?:png|jpe?g|gif|webp|bmp);base64,[A-Za-z0-9+/]+={0,2}$/i;
const URL_ATTRIBUTES = [
  "action",
  "background",
  "cite",
  "data",
  "formaction",
  "href",
  "longdesc",
  "ping",
  "poster",
  "profile",
  "src",
  "srcset",
  "xlink:href",
] as const;

function stripNetworkCapableMarkup(document: Document): void {
  for (const style of document.querySelectorAll("style")) {
    if (/@import\b|url\s*\(/i.test(style.textContent ?? "")) style.remove();
  }

  for (const element of document.querySelectorAll<HTMLElement>("*")) {
    if (/@import\b|url\s*\(/i.test(element.getAttribute("style") ?? "")) {
      element.removeAttribute("style");
    }
    for (const attribute of URL_ATTRIBUTES) {
      const value = element.getAttribute(attribute)?.trim();
      if (!value) continue;
      const safeFragment = attribute === "href" && value.startsWith("#");
      const safeImage =
        attribute === "src" &&
        element.localName === "img" &&
        SAFE_IMAGE_DATA_URL.test(value);
      if (!safeFragment && !safeImage) element.removeAttribute(attribute);
    }
  }
}

export function prepareHtmlPreviewContent(content: string): string {
  const sanitized = DOMPurify.sanitize(content, {
    WHOLE_DOCUMENT: true,
    USE_PROFILES: { html: true },
    FORBID_TAGS: [
      "base",
      "embed",
      "form",
      "iframe",
      "link",
      "meta",
      "object",
      "script",
    ],
    FORBID_ATTR: ["srcset", "ping", "action", "formaction"],
    ALLOW_DATA_ATTR: false,
  });
  const document = new DOMParser().parseFromString(sanitized, "text/html");
  stripNetworkCapableMarkup(document);

  const base = document.createElement("base");
  base.href = "about:srcdoc";
  const csp = document.createElement("meta");
  csp.httpEquiv = "Content-Security-Policy";
  csp.content = HTML_PREVIEW_CSP;
  const referrer = document.createElement("meta");
  referrer.name = "referrer";
  referrer.content = "no-referrer";
  document.head.prepend(base, csp, referrer);

  return `<!doctype html>${document.documentElement.outerHTML}`.replace(
    /<base href="about:srcdoc">/i,
    SAFE_SRC_DOC_BASE,
  );
}
