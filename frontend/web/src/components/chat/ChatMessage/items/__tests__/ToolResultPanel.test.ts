import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("mobile tool result panel slide-in keeps the sheet opaque", () => {
  const componentSource = readFileSync(
    new URL("../ToolResultPanel.tsx", import.meta.url),
    "utf8",
  );
  const animationsSource = readFileSync(
    new URL("../../../../../styles/animations.css", import.meta.url),
    "utf8",
  );
  const slideUpAnimation = animationsSource.match(
    /@keyframes\s+slide-up-fullscreen\s*\{(?<body>[\s\S]*?)\n\}/,
  )?.groups?.body;

  assert.ok(slideUpAnimation, "slide-up-fullscreen animation should exist");
  assert.doesNotMatch(
    slideUpAnimation,
    /\bopacity\s*:/,
    "sliding the mobile sheet should not reveal content underneath",
  );
  assert.doesNotMatch(
    componentSource,
    /transform:\s*"translateY\(100%\)"\s*,\s*opacity:\s*0/,
    "pre-animation mobile sheet state should keep its opaque background",
  );
});

test("mobile swipe-to-close is limited to the explicit drag handle", () => {
  const componentSource = readFileSync(
    new URL("../ToolResultPanel.tsx", import.meta.url),
    "utf8",
  );
  const swipeHookSource = readFileSync(
    new URL("../../../../../hooks/useSwipeToClose.ts", import.meta.url),
    "utf8",
  );
  const sidebarPanelHookSource = readFileSync(
    new URL("../../../../../hooks/useSidebarPanel.ts", import.meta.url),
    "utf8",
  );

  assert.match(
    swipeHookSource,
    /dragHandleRef\?: RefObject<HTMLElement \| null>/,
    "swipe hook should support an explicit drag handle ref",
  );
  assert.match(
    sidebarPanelHookSource,
    /dragHandleRef,\s*\}\);/,
    "sidebar panel hook should pass its drag handle into the swipe hook",
  );
  assert.match(
    componentSource,
    /ref=\{dragHandleRef\}/,
    "tool result panel should attach the swipe handle ref to the visible mobile handle",
  );
});

test("explicit close button reports a user close before closing the panel", () => {
  const componentSource = readFileSync(
    new URL("../ToolResultPanel.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    componentSource,
    /onUserClose\?: \(\) => void/,
    "tool result panel should expose an explicit user-close callback",
  );
  assert.match(
    componentSource,
    /const handleUserClose = useCallback\(\(\) => \{\s*onUserClose\?\.\(\);\s*clearSidebarHistory\(\);\s*onClose\(\);/s,
    "close button should notify user-close handlers before closing",
  );
  assert.match(
    componentSource,
    /useSidebarPanel\(\{\s*open,\s*onClose: handleUserClose,/s,
    "keyboard and swipe close paths should use the same user-close handler",
  );
});
