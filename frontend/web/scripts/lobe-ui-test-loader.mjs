// Native Node resolves this peer's CommonJS main; Vite resolves its ESM module.
// Preserve the real exports while matching the default component seen by Vite.
const prefix = "lobe-ui-node-table:";
export async function resolve(specifier, context, nextResolve) {
  const result = await nextResolve(specifier, context);
  if (specifier !== "@rc-component/table") return result;
  return { url: prefix + encodeURIComponent(result.url), shortCircuit: true };
}

export async function load(url, context, nextLoad) {
  if (!url.startsWith(prefix)) return nextLoad(url, context);
  const target = JSON.stringify(decodeURIComponent(url.slice(prefix.length)));
  return {
    format: "module",
    shortCircuit: true,
    source: `import peer from ${target}; export * from ${target}; export default peer.default ?? peer;`,
  };
}
