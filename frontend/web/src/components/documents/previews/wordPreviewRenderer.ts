import type { Options as DocxPreviewOptions } from "docx-preview";

type MammothHtmlResult = {
  value: string;
};

export type DocxPreviewRenderAsync = (
  data: ArrayBuffer,
  bodyContainer: HTMLElement,
  styleContainer?: HTMLElement,
  options?: Partial<DocxPreviewOptions>,
) => Promise<unknown>;

export type MammothConvertToHtml = (
  input: { arrayBuffer: ArrayBuffer },
  options?: Record<string, unknown>,
) => Promise<MammothHtmlResult>;

interface RenderDocxPreviewHtmlInput {
  arrayBuffer: ArrayBuffer;
  renderAsync: DocxPreviewRenderAsync;
  convertToHtml: MammothConvertToHtml;
  onDocxPreviewError?: (error: unknown) => void;
}

export type WordPreviewRenderResult =
  | { kind: "docx-preview"; html: string }
  | { kind: "html"; html: string };

const mammothStyleMap = [
  "p[style-name='Heading 1'] => h1:fresh",
  "p[style-name='Heading 2'] => h2:fresh",
  "p[style-name='Heading 3'] => h3:fresh",
  "p[style-name='Heading 4'] => h4:fresh",
  "b => strong",
  "i => em",
  "u => u",
];

export function createWordPreviewRendererOptions(): Partial<DocxPreviewOptions> {
  return {
    className: "docx",
    inWrapper: true,
    ignoreWidth: false,
    ignoreHeight: false,
    ignoreFonts: false,
    breakPages: true,
    renderHeaders: true,
    renderFooters: true,
    renderFootnotes: true,
    renderEndnotes: true,
    renderComments: false,
    renderChanges: false,
    renderAltChunks: false,
    useBase64URL: true,
  };
}

export async function renderDocxPreviewHtml({
  arrayBuffer,
  renderAsync,
  convertToHtml,
  onDocxPreviewError = (error) => {
    console.warn("docx-preview failed, falling back to mammoth:", error);
  },
}: RenderDocxPreviewHtmlInput): Promise<WordPreviewRenderResult> {
  // A template owns inert nodes: untrusted document styles never enter the
  // application DOM before the final sandboxed, CSP-protected iframe.
  const template = document.createElement("template");
  const container = document.createElement("div");
  template.content.appendChild(container);

  try {
    await renderAsync(
      arrayBuffer,
      container,
      container,
      createWordPreviewRendererOptions(),
    );
    if (container.textContent?.trim() || container.innerHTML.trim()) {
      return { kind: "docx-preview", html: container.innerHTML };
    }
  } catch (error) {
    onDocxPreviewError(error);
  }

  container.innerHTML = "";
  const result = await convertToHtml(
    { arrayBuffer },
    { styleMap: mammothStyleMap },
  );
  return { kind: "html", html: result.value };
}
