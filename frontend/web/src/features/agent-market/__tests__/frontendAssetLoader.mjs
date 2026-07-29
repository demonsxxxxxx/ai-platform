export async function load(url, context, nextLoad) {
  const assetUrl = url.split("?")[0];
  if (assetUrl.endsWith(".svg") || assetUrl.endsWith(".css")) {
    return {
      format: "module",
      source: "export default '';",
      shortCircuit: true,
    };
  }
  return nextLoad(url, context);
}
