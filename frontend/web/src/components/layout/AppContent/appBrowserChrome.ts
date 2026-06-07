export function shouldNudgeBrowserChrome({
  isMobileDevice,
  isStandaloneDisplayMode,
  hasVisualViewport,
}: {
  isMobileDevice: boolean;
  isStandaloneDisplayMode: boolean;
  hasVisualViewport: boolean;
}): boolean {
  return isMobileDevice && hasVisualViewport && !isStandaloneDisplayMode;
}

export function getBrowserChromeNudgeScrollY({
  scrollHeight,
  innerHeight,
}: {
  scrollHeight: number;
  innerHeight: number;
}): number {
  return scrollHeight > innerHeight ? 1 : 0;
}
