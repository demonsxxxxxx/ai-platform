import { Buffer } from "buffer";
import process from "process/browser";

interface ExtractedWordDocument {
  getBody: () => string;
  getHeaders?: () => string;
  getFooters?: () => string;
  getFootnotes?: () => string;
  getEndnotes?: () => string;
  getAnnotations?: () => string;
  getTextboxes?: () => string;
}

type Constructor<T> = new (...args: unknown[]) => T;

function getDefaultExport<T>(module: T | { default?: T }): T {
  if (
    typeof module === "object" &&
    module !== null &&
    "default" in module &&
    module.default
  ) {
    return module.default;
  }
  return module as T;
}

export function isLegacyDocArrayBuffer(arrayBuffer: ArrayBuffer): boolean {
  const signature = new Uint8Array(
    arrayBuffer,
    0,
    Math.min(8, arrayBuffer.byteLength),
  );
  return (
    signature.length >= 8 &&
    signature[0] === 0xd0 &&
    signature[1] === 0xcf &&
    signature[2] === 0x11 &&
    signature[3] === 0xe0 &&
    signature[4] === 0xa1 &&
    signature[5] === 0xb1 &&
    signature[6] === 0x1a &&
    signature[7] === 0xe1
  );
}

export async function extractLegacyDocText(
  arrayBuffer: ArrayBuffer,
): Promise<string> {
  if (!isLegacyDocArrayBuffer(arrayBuffer)) {
    throw new Error("Not a legacy Word document");
  }

  (globalThis as typeof globalThis & { Buffer?: typeof Buffer }).Buffer ??=
    Buffer;
  (
    globalThis as typeof globalThis & {
      process?: typeof process;
    }
  ).process ??= process;

  const [extractorModule, readerModule] = await Promise.all([
    import("word-extractor/lib/word-ole-extractor"),
    import("word-extractor/lib/buffer-reader"),
  ]);
  const WordOleExtractor = getDefaultExport(extractorModule) as Constructor<{
    extract: (reader: unknown) => Promise<ExtractedWordDocument>;
  }>;
  const BufferReader = getDefaultExport(readerModule) as Constructor<unknown>;

  const nodeBuffer = Buffer.from(new Uint8Array(arrayBuffer));
  const reader = new BufferReader(nodeBuffer);
  const document = await new WordOleExtractor().extract(reader);
  const sections = [
    document.getHeaders?.(),
    document.getBody(),
    document.getTextboxes?.(),
    document.getFootnotes?.(),
    document.getEndnotes?.(),
    document.getAnnotations?.(),
    document.getFooters?.(),
  ];
  const text = sections
    .map((section) => section?.trim())
    .filter(Boolean)
    .join("\n\n")
    .trim();

  if (!text) {
    throw new Error("No readable text found in document");
  }
  return text;
}
