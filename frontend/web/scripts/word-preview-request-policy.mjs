// Only this synthetic fixture and Vite module/style resources may use HTTP.
// A same-origin document URL is still untrusted, especially before adoption.
export function isWordFixtureAsset(rawUrl, resourceType, origins) {
  const url = new URL(rawUrl);
  if (url.pathname.startsWith('/__word_probe__/')) return false;
  if (!origins.includes(url.origin)) return false;
  if (resourceType === 'document') return [
    '/scripts/fixtures/word-preview/index.html',
    '/scripts/fixtures/word-preview/control.html',
    '/scripts/fixtures/word-preview/nested-control.html',
  ].includes(url.pathname);
  return ['script', 'stylesheet'].includes(resourceType) && [
    '/src/', '/node_modules/', '/@vite/', '/@react-refresh',
    '/scripts/fixtures/word-preview/',
  ].some(prefix => url.pathname.startsWith(prefix));
}
