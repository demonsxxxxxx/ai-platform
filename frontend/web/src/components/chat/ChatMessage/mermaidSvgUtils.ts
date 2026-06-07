const FULLSCREEN_FALLBACKS: Array<[string, string]> = [
  ["display", "block"],
  ["width", "auto"],
  ["height", "auto"],
  ["min-width", "200px"],
  ["min-height", "100px"],
  ["max-height", "85vh"],
];

export function stripResponsiveWidthAttribute(svg: string): string {
  return svg.replace(/\swidth="100%"/g, "");
}

export function prepareFullscreenMermaidSvg(svg: string): string {
  const xmlSafeSvg = svg.replace(/<br(\s[^>]*?)?>/g, (_match, attrs = "") => {
    const trimmedAttrs = attrs.trim();
    return trimmedAttrs ? `<br ${trimmedAttrs} />` : "<br />";
  });

  return xmlSafeSvg.replace(/<svg\b([^>]*)>/, (_match, attrs) => {
    const styleMatch = attrs.match(/\sstyle="([^"]*)"/);
    const declarations = new Map<string, string>();

    if (styleMatch) {
      for (const declaration of styleMatch[1].split(";")) {
        const trimmed = declaration.trim();
        if (!trimmed) continue;
        const separatorIndex = trimmed.indexOf(":");
        if (separatorIndex === -1) continue;
        const property = trimmed.slice(0, separatorIndex).trim();
        const value = trimmed.slice(separatorIndex + 1).trim();
        if (!property || !value) continue;
        declarations.set(property, value);
      }
    }

    for (const [property, value] of FULLSCREEN_FALLBACKS) {
      if (!declarations.has(property)) {
        declarations.set(property, value);
      }
    }

    const nextStyle = Array.from(declarations.entries())
      .map(([property, value]) => `${property}: ${value}`)
      .join("; ");
    const styleAttribute = ` style="${nextStyle};"`;

    if (styleMatch) {
      return `<svg${attrs.replace(/\sstyle="([^"]*)"/, styleAttribute)}>`;
    }

    return `<svg${attrs}${styleAttribute}>`;
  });
}
