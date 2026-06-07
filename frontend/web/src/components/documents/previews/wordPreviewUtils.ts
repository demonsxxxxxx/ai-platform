import JSZip from "jszip";

const XML_ENTITY_MAP: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
};

function decodeXmlEntities(value: string): string {
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, entity) => {
    if (entity.startsWith("#x")) {
      const codePoint = Number.parseInt(entity.slice(2), 16);
      return Number.isFinite(codePoint)
        ? String.fromCodePoint(codePoint)
        : match;
    }
    if (entity.startsWith("#")) {
      const codePoint = Number.parseInt(entity.slice(1), 10);
      return Number.isFinite(codePoint)
        ? String.fromCodePoint(codePoint)
        : match;
    }
    return XML_ENTITY_MAP[entity.toLowerCase()] ?? match;
  });
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function docxTextToHtml(text: string): string {
  return text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map(
      (paragraph) => `<p>${escapeHtml(paragraph).replace(/\n/g, "<br />")}</p>`,
    )
    .join("");
}

function xmlHasInvalidTagName(xml: string): boolean {
  const tagRegex = /<\s*(\/?)([^!?/\s>][^\s/>]*)/g;

  for (const match of xml.matchAll(tagRegex)) {
    const tagName = match[2];
    if (!tagName || !/^[:A-Z_a-z]/.test(tagName)) {
      return true;
    }
  }

  return false;
}

export async function isDocxSafeForMammoth(
  arrayBuffer: ArrayBuffer,
): Promise<boolean> {
  const zip = await JSZip.loadAsync(arrayBuffer);
  const xmlFiles = Object.values(zip.files).filter(
    (file) =>
      !file.dir && (file.name.endsWith(".xml") || file.name.endsWith(".rels")),
  );

  for (const file of xmlFiles) {
    const xml = await file.async("string");
    if (xmlHasInvalidTagName(xml)) {
      return false;
    }
  }

  return true;
}

export async function extractDocxTextFallback(
  arrayBuffer: ArrayBuffer,
): Promise<string> {
  const zip = await JSZip.loadAsync(arrayBuffer);
  const documentXml = await zip.file("word/document.xml")?.async("string");
  if (!documentXml) {
    return "";
  }

  const paragraphs: string[] = [];
  const paragraphRegex = /<w:p\b[\s\S]*?(?:<\/w:p>|$)/g;
  const textRegex = /<w:t\b[^>]*>([\s\S]*?)(?:<\/w:t>|$)/g;
  for (const paragraphMatch of documentXml.matchAll(paragraphRegex)) {
    const textParts = Array.from(
      paragraphMatch[0].matchAll(textRegex),
      (match) => decodeXmlEntities(match[1]),
    );
    const paragraphText = textParts.join("");
    if (paragraphText.trim()) {
      paragraphs.push(paragraphText);
    }
  }

  return paragraphs.join("\n\n");
}
