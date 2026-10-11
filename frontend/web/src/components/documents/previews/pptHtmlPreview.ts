import DOMPurify from "dompurify";

const IMAGE_DATA_URI_RE =
  /data:image\/(?:png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=]+)/gi;
const SAFE_EMBEDDED_IMAGE_RE =
  /^data:image\/(?:png|jpe?g|gif|webp|bmp|svg\+xml);base64,/i;
const PPT_IFRAME_CSP =
  "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:";

function decodeBase64Header(value: string): string {
  try {
    return globalThis.atob(value.slice(0, 256));
  } catch {
    return "";
  }
}

export function normalizePptxRenderedHtml(html: string): string {
  return html.replace(IMAGE_DATA_URI_RE, (match, payload: string) => {
    const header = decodeBase64Header(payload).trimStart();
    return /^(?:<\?xml\b[^>]*>\s*)?<svg(?:\s|>)/i.test(header)
      ? `data:image/svg+xml;base64,${payload}`
      : match;
  });
}

export function sanitizePptxRenderedHtml(html: string): string {
  const sanitized = DOMPurify.sanitize(normalizePptxRenderedHtml(html), {
    USE_PROFILES: { html: true, svg: true, svgFilters: true },
    FORBID_TAGS: [
      "base",
      "embed",
      "form",
      "iframe",
      "link",
      "meta",
      "object",
      "script",
      "style",
    ],
    ALLOW_DATA_ATTR: false,
  });
  const template = document.createElement("template");
  template.innerHTML = sanitized;

  for (const element of template.content.querySelectorAll<HTMLElement>(
    "[href], [xlink\\:href], [src], [style]",
  )) {
    const style = element.getAttribute("style");
    if (style && /url\s*\(/i.test(style)) element.removeAttribute("style");

    for (const attribute of ["href", "xlink:href", "src"] as const) {
      const value = element.getAttribute(attribute)?.trim();
      if (!value) continue;
      const isLocalReference = value.startsWith("#");
      const isEmbeddedImage =
        (attribute === "src" || element.localName === "image") &&
        SAFE_EMBEDDED_IMAGE_RE.test(value);
      if (!isLocalReference && !isEmbeddedImage) {
        element.removeAttribute(attribute);
      }
    }
  }

  return template.innerHTML;
}

export function preparePptxSlideDocument(html: string): string {
  const content = sanitizePptxRenderedHtml(html);
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${PPT_IFRAME_CSP}"><meta name="referrer" content="no-referrer"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:white}body{position:relative}</style></head><body>${content}</body></html>`;
}
