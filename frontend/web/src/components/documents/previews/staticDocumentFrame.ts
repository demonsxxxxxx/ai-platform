/** Enclose already-sanitized document markup before it reaches a sandboxed iframe. */
export function buildStaticDocumentFrame(input: {
  content: string;
  styles?: string;
  allowBlobFonts?: boolean;
}): string {
  const fontSources = input.allowBlobFonts ? "data: blob:" : "data:";
  const csp = `default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src ${fontSources}; connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'`;
  return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="referrer" content="no-referrer"><style>${input.styles ?? ""}</style></head><body>${input.content}</body></html>`;
}
