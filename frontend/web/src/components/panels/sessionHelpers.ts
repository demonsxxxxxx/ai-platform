/**
 * Session display helpers
 */

import type { BackendSession } from "../../services/api";
import type { AgentConversationIdentity } from "../../types/agentProfile";
import { DEFAULT_CHAT_AGENT_ID } from "../../services/api/session";
import type { TFunction } from "i18next";
import { parseDate } from "../../utils/datetime";

export function getSessionTitle(session: BackendSession, t: TFunction): string {
  if (session.name) return session.name;
  const meta = session.metadata as Record<string, unknown>;
  if (meta?.title) return meta.title as string;
  return t("sidebar.newChat");
}

export interface AgentSessionGroup {
  key: string;
  name: string;
  identity: AgentConversationIdentity | null;
  sessions: BackendSession[];
}

function sessionTimestamp(session: BackendSession): number {
  for (const value of [session.updated_at, session.created_at]) {
    const timestamp = Date.parse(value);
    if (Number.isFinite(timestamp)) return timestamp;
  }
  return 0;
}

function fallbackAgentName(session: BackendSession): string {
  if (session.agent_id === DEFAULT_CHAT_AGENT_ID) return "通用助手";
  return "其他专家";
}

/** Group the global history by public Agent identity, newest session first. */
export function groupSessionsByAgent(
  sessionList: BackendSession[],
): AgentSessionGroup[] {
  const groups = new Map<string, BackendSession[]>();
  for (const session of sessionList) {
    const key = session.agent_conversation?.agent_id ?? session.agent_id;
    const group = groups.get(key);
    if (group) group.push(session);
    else groups.set(key, [session]);
  }

  return [...groups.entries()]
    .map(([key, sessions]) => {
      sessions.sort((left, right) => {
        const timestampDifference =
          sessionTimestamp(right) - sessionTimestamp(left);
        return timestampDifference || right.id.localeCompare(left.id);
      });
      const identity = sessions.find((session) => session.agent_conversation)
        ?.agent_conversation;
      return {
        key,
        name: identity?.name ?? fallbackAgentName(sessions[0]),
        identity: identity ?? null,
        sessions,
      };
    })
    .sort((left, right) => {
      const leftTimestamp = sessionTimestamp(left.sessions[0]);
      const rightTimestamp = sessionTimestamp(right.sessions[0]);
      return rightTimestamp - leftTimestamp || left.key.localeCompare(right.key);
    });
}

export function groupSessionsByTime(
  sessionList: BackendSession[],
  t: TFunction,
): { label: string; sessions: BackendSession[] }[] {
  const groups: { label: string; sessions: BackendSession[] }[] = [];
  const today: BackendSession[] = [];
  const yesterday: BackendSession[] = [];
  const thisWeek: BackendSession[] = [];
  const older: BackendSession[] = [];

  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart.getTime() - 24 * 60 * 60 * 1000);
  const weekStart = new Date(todayStart.getTime() - 7 * 24 * 60 * 60 * 1000);

  sessionList.forEach((session) => {
    const sessionDate = parseDate(session.updated_at);
    if (sessionDate >= todayStart) {
      today.push(session);
    } else if (sessionDate >= yesterdayStart) {
      yesterday.push(session);
    } else if (sessionDate >= weekStart) {
      thisWeek.push(session);
    } else {
      older.push(session);
    }
  });

  if (today.length > 0)
    groups.push({ label: t("sidebar.today"), sessions: today });
  if (yesterday.length > 0)
    groups.push({ label: t("sidebar.yesterday"), sessions: yesterday });
  if (thisWeek.length > 0)
    groups.push({ label: t("sidebar.previous7Days"), sessions: thisWeek });
  if (older.length > 0)
    groups.push({ label: t("sidebar.older"), sessions: older });

  return groups;
}
