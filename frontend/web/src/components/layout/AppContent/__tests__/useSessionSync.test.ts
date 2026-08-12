import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  getInitialUrlSyncCompletionAction,
  getSessionRouteSyncAction,
  shouldClearConversationOnRouteIdentityChange,
  shouldLoadSessionFromUrlChange,
} from "../useSessionSync.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

test("does not restore a chat route after the user already navigated away", () => {
  assert.equal(
    getSessionRouteSyncAction({
      activeTab: "chat",
      pathname: "/skills",
      sessionId: "session-123",
      urlSessionId: undefined,
      externalNavigate: false,
    }),
    null,
  );
});

test("does not restore chat when render state is stale but browser path already left chat", () => {
  assert.equal(
    getSessionRouteSyncAction({
      activeTab: "chat",
      pathname: "/chat/session-123",
      browserPathname: "/users",
      sessionId: "session-456",
      urlSessionId: "session-123",
      externalNavigate: false,
    }),
    null,
  );
});

test("updates the chat url when a new session is created from /chat", () => {
  assert.deepEqual(
    getSessionRouteSyncAction({
      activeTab: "chat",
      pathname: "/chat",
      sessionId: "session-123",
      urlSessionId: undefined,
      externalNavigate: false,
    }),
    {
      type: "replace-url",
      path: "/chat/session-123",
    },
  );
});

test("preserves an accepted generic session when its URL is canonicalized", () => {
  const liveState = {
    sessionId: "session-123",
    authoritySessionId: "session-123",
    messages: ["first user turn", "first assistant turn"],
  };
  const action = getSessionRouteSyncAction({
    activeTab: "chat",
    pathname: "/chat",
    sessionId: liveState.sessionId,
    urlSessionId: undefined,
    externalNavigate: false,
  });

  assert.deepEqual(action, {
    type: "replace-url",
    path: "/chat/session-123",
  });
  assert.equal(
    shouldClearConversationOnRouteIdentityChange({
      hasAgentWorkspace: false,
      routeSessionId: "session-123",
      sessionId: liveState.sessionId,
    }),
    false,
  );
  assert.equal(liveState.authoritySessionId, liveState.sessionId);
  assert.deepEqual(liveState.messages, [
    "first user turn",
    "first assistant turn",
  ]);
});

test("clears and reloads when external navigation selects another session", () => {
  assert.equal(
    shouldClearConversationOnRouteIdentityChange({
      hasAgentWorkspace: false,
      routeSessionId: "session-b",
      sessionId: "session-a",
    }),
    true,
  );
  assert.equal(
    shouldLoadSessionFromUrlChange({
      activeTab: "chat",
      sessionId: null,
      urlSessionId: "session-b",
      isLoading: false,
      isNewSession: false,
      isInternalNavigation: false,
    }),
    true,
  );
});

test("Agent first-send canonicalization retains the already bound Session", () => {
  assert.equal(
    shouldClearConversationOnRouteIdentityChange({
      hasAgentWorkspace: true,
      routeSessionId: undefined,
      sessionId: "session-agent",
    }),
    false,
  );
  assert.equal(
    shouldClearConversationOnRouteIdentityChange({
      hasAgentWorkspace: true,
      routeSessionId: "session-agent",
      sessionId: "session-agent",
    }),
    false,
  );
  assert.equal(
    shouldClearConversationOnRouteIdentityChange({
      hasAgentWorkspace: true,
      routeSessionId: "session-other",
      sessionId: "session-agent",
    }),
    true,
  );
});

test("updates a published Agent workspace URL without falling back to generic chat", () => {
  assert.deepEqual(
    getSessionRouteSyncAction({
      activeTab: "chat",
      pathname: "/agent-market/agt_support/4/chat",
      sessionRouteBasePath: "/agent-market/agt_support/4/chat",
      sessionId: "session-123",
      urlSessionId: undefined,
      externalNavigate: false,
    }),
    {
      type: "replace-url",
      path: "/agent-market/agt_support/4/chat/session-123",
    },
  );
});

test("loads the target session when external navigation lands on chat from an empty state", () => {
  assert.equal(
    shouldLoadSessionFromUrlChange({
      activeTab: "chat",
      sessionId: null,
      urlSessionId: "session-123",
      isLoading: false,
      isNewSession: false,
      isInternalNavigation: false,
    }),
    true,
  );
});

test("does not trigger a second url-change load while the initial url sync is still pending", () => {
  assert.equal(
    shouldLoadSessionFromUrlChange({
      activeTab: "chat",
      sessionId: null,
      urlSessionId: "session-123",
      isLoading: false,
      isNewSession: false,
      isInternalNavigation: false,
      initialUrlSyncPending: true,
    }),
    false,
  );
});

test("waits for the Agent workspace binding before loading a URL Session", () => {
  assert.equal(
    shouldLoadSessionFromUrlChange({
      activeTab: "chat",
      sessionId: null,
      urlSessionId: "session-agent-a",
      isLoading: false,
      isNewSession: false,
      isInternalNavigation: false,
      historyLoadEnabled: false,
    }),
    false,
  );
});

test("clears external navigation state after the initial url sync finishes on chat", () => {
  assert.deepEqual(
    getInitialUrlSyncCompletionAction({
      activeTab: "chat",
      pathname: "/chat/session-123",
      externalNavigate: true,
    }),
    {
      type: "clear-external-state",
      path: "/chat/session-123",
    },
  );
});

test("session selection does not issue page-level scroll resets", () => {
  const source = readFileSync(
    resolve(__dirname, "../useSessionSync.ts"),
    "utf8",
  );

  assert.doesNotMatch(source, /window\.scrollTo/);
});
