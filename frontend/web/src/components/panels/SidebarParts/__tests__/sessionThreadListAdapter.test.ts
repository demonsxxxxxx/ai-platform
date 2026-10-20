import assert from "node:assert/strict";
import test from "node:test";

import type { BackendSession } from "../../../../services/api.ts";
import {
  createSessionThreadListAdapter,
  toSessionThreadData,
  toSessionThreadListItem,
} from "../sessionThreadListAdapter.ts";

function session(overrides: Partial<BackendSession> = {}): BackendSession {
  return {
    id: "session-1",
    agent_id: "general-agent",
    created_at: "2026-10-19T08:00:00Z",
    updated_at: "2026-10-19T09:30:00Z",
    is_active: true,
    metadata: {},
    ...overrides,
  };
}

test("session adapter maps public title, unread state, and route identity", () => {
  const item = toSessionThreadListItem(
    session({ name: "专家任务", unread_count: 3 }),
    "新对话",
  );
  const thread = toSessionThreadData(session({ name: "专家任务" }), "新对话");

  assert.equal(item.id, "session-1");
  assert.equal(item.title, "专家任务");
  assert.equal(
    item.time,
    new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }).format(Date.parse("2026-10-19T09:30:00Z")),
  );
  assert.equal(item.unread, true);
  assert.equal(item.unreadCount, 3);
  assert.deepEqual(thread, {
    id: "session-1",
    remoteId: "session-1",
    title: "专家任务",
    status: "regular",
  });
});

test("session adapter preserves source paging and delegates actions without fetching", async () => {
  const calls: string[] = [];
  const source = {
    sessions: [session({ id: "first", name: "First" })],
    isLoading: true,
    isLoadingMore: false,
    hasMore: true,
    loadMore: async () => {
      calls.push("loadMore");
    },
  };
  const adapter = createSessionThreadListAdapter({
    source,
    currentSessionId: "first",
    fallbackTitle: "新对话",
    actions: {
      onSelectSession: (id) => {
        calls.push(`select:${id}`);
      },
      onRenameSession: (id, title) => {
        calls.push(`rename:${id}:${title}`);
      },
      onDeleteSession: (id) => {
        calls.push(`delete:${id}`);
      },
    },
  });

  assert.equal(adapter.adapter.threadId, "first");
  assert.equal(adapter.adapter.isLoading, true);
  assert.equal(adapter.hasMore, true);
  assert.deepEqual(adapter.adapter.threads?.map(({ id, title }) => [id, title]), [
    ["first", "First"],
  ]);
  await adapter.loadMore();
  await adapter.adapter.onSwitchToThread?.("first");
  await adapter.adapter.onRename?.("first", "Renamed");
  await adapter.adapter.onDelete?.("first");
  assert.deepEqual(calls, [
    "loadMore",
    "select:first",
    "rename:first:Renamed",
    "delete:first",
  ]);
});

test("session adapter uses metadata title and hides invalid timestamps", () => {
  const item = toSessionThreadListItem(
    session({ name: undefined, metadata: { title: "Metadata title" }, updated_at: "" }),
    "新对话",
  );
  assert.equal(item.title, "Metadata title");
  assert.equal(item.time, "");
  assert.equal(item.unread, false);
});
