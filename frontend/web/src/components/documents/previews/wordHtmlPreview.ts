import DOMPurify from "dompurify";
import { buildStaticDocumentFrame } from "./staticDocumentFrame";

const LOCAL_IMAGE = /^data:(?:image\/(?:png|jpe?g|gif|webp|bmp|svg\+xml)|application\/octet-stream);base64,/i;

/** DOMPurify removes executable markup; iframe CSP owns CSS/font network isolation. */
export function prepareWordPreviewDocument(input: {
  html: string;
  styles?: string;
  className?: string;
}): string {
  const sanitized = DOMPurify.sanitize(input.html, {
    USE_PROFILES: { html: true, svg: true, svgFilters: true },
    ADD_TAGS: ["style"],
    FORCE_BODY: true,
    FORBID_TAGS: ["base", "embed", "form", "iframe", "link", "meta", "object", "script"],
    FORBID_ATTR: ["srcset", "ping", "action", "formaction"],
    ALLOW_DATA_ATTR: false,
  });
  const template = document.createElement("template");
  template.innerHTML = sanitized;
  // A static preview is not a browsing surface. Keep local anchors and embedded
  // images, but do not let links navigate even the sandboxed frame to the network.
  for (const element of template.content.querySelectorAll("*")) {
    for (const attribute of ["href", "xlink:href", "src", "poster", "background", "data"]) {
      const value = element.getAttribute(attribute)?.trim();
      if (!value) continue;
      const localAnchor = attribute === "href" && value.startsWith("#");
      const localImage = (element.localName === "img" || element.localName === "image") &&
        (LOCAL_IMAGE.test(value) || value.startsWith("blob:"));
      if (!localAnchor && !localImage) element.removeAttribute(attribute);
    }
  }
  const wrapper = document.createElement("div");
  wrapper.className = input.className ?? "docx-preview-content";
  wrapper.append(template.content);
  return buildStaticDocumentFrame({
    content: wrapper.outerHTML,
    allowBlobFonts: true,
    styles: `html,body{margin:0;min-height:100%;background:white}body{overflow:auto}${input.styles ?? ""}`,
  });
}
