import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const chatViewSource = readFileSync(
  resolve(
    process.cwd(),
    "src",
    "components",
    "layout",
    "AppContent",
    "ChatView.tsx",
  ),
  "utf8",
);
const chatInputSource = readFileSync(
  resolve(process.cwd(), "src", "components", "chat", "ChatInput.tsx"),
  "utf8",
);

test("drives the Virtuoso session key through state so session switches remount the message list", () => {
  assert.match(chatViewSource, /setMessageListSessionKey/);
  assert.match(chatViewSource, /key=\{messageListSessionKey\}/);
  assert.doesNotMatch(
    chatViewSource,
    /key=\{messageListSessionKeyRef\.current\}/,
  );
});

test("passes the message list session key into the scroll hook as a bottom-lock token", () => {
  assert.match(
    chatViewSource,
    /useMessageScroll\([\s\S]*isLoadingHistory,\s*messageListSessionKey,\s*\)/,
  );
});

test("keeps the assistant-ui message component stable across streaming updates", () => {
  assert.match(
    chatViewSource,
    /const ASSISTANT_UI_MESSAGE_COMPONENTS = \{\s*Message: AssistantUiProjectedMessage,\s*\};/,
  );
  assert.match(
    chatViewSource,
    /useContext\(AssistantUiMessageContentContext\)/,
  );
  assert.match(
    chatViewSource,
    /<AssistantUiMessageContentContext\.Provider[\s\S]*?<ThreadPrimitive\.Unstable_MessageById[\s\S]*?<\/AssistantUiMessageContentContext\.Provider>/,
  );
  assert.match(
    chatViewSource,
    /components=\{ASSISTANT_UI_MESSAGE_COMPONENTS\}/,
  );
  assert.doesNotMatch(chatViewSource, /components=\{\{\s*Message:/);
});

test("passes an authenticated session scope through virtualized message rows", () => {
  assert.match(chatViewSource, /createArtifactDownloadScopeContext/);
  assert.match(
    chatViewSource,
    /artifactDownloadScopeContext=\{artifactDownloadScopeContext\}/,
  );
  assert.match(chatViewSource, /clearArtifactDownloadScope\(previousScope\)/);
});

test("anchors floating scroll buttons to the chat input", () => {
  assert.match(
    chatViewSource,
    /const FLOATING_SCROLL_BUTTON_OFFSET_CLASS = "bottom-full mb-3";/,
  );
  assert.equal(
    chatViewSource.match(/\$\{FLOATING_SCROLL_BUTTON_OFFSET_CLASS\}/g)?.length,
    2,
  );
  assert.match(chatViewSource, /const composer = \(\s*<div className="relative"/);
  assert.match(chatViewSource, /<ChatInput\s+\{\.\.\.chatInputProps\}/);
  assert.doesNotMatch(chatViewSource, /bottom-\d+/);
});

test("keeps live composer draft state out of ChatView", () => {
  assert.doesNotMatch(chatViewSource, /const \[composerDraft, setComposerDraft\]/);
  assert.doesNotMatch(chatViewSource, /pendingComposerInput/);
  assert.match(
    chatViewSource,
    /draftSnapshotRef:\s*composerDraftSnapshotRef/,
  );
  assert.match(chatViewSource, /draftScopeKey:\s*sessionId/);
  assert.match(
    chatInputSource,
    /const \[input, setLocalInput\] = useState\(inputRef\.current\)/,
  );
  assert.match(
    chatInputSource,
    /if \(!initialDraft \|\| !initialDraftKey\) return;/,
  );
  assert.match(
    chatInputSource,
    /setInput\(\(current\) => current \|\| initialDraft\)/,
  );
});

test("projects visible-range changes only into an open message outline", () => {
  assert.match(
    chatViewSource,
    /const visibleRangeRef = useRef<ListRange \| null>\(null\)/,
  );
  assert.doesNotMatch(chatViewSource, /const \[visibleRange, setVisibleRange\]/);
  assert.doesNotMatch(chatViewSource, /setVisibleRange\(/);
  assert.match(
    chatViewSource,
    /if \(!isPersistentToolPanelOpen\("outline"\)\) return;/,
  );
});
