import { useEffect, useRef, useState } from "react";
import type { ThreadSuggestion } from "@assistant-ui/react";
import type { Message } from "../../../types";
import { sessionApi } from "../../../services/api/session";
import { followUpOwnerKey, settledFollowUpAnswer } from "./followUpAnswer";

export const FOLLOW_UP_SUGGESTION_LIMIT = 3;

function toSuggestions(prompts: unknown): ThreadSuggestion[] {
  if (!Array.isArray(prompts)) return [];
  const seen = new Set<string>();
  const suggestions: ThreadSuggestion[] = [];
  for (const prompt of prompts) {
    if (typeof prompt !== "string") continue;
    const text = prompt.trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    suggestions.push({ prompt: text });
    if (suggestions.length === FOLLOW_UP_SUGGESTION_LIMIT) break;
  }
  return suggestions;
}

/**
 * Ask once for model-generated questions about the answer that settled in this view.
 * Restored history never requests; an account, session or turn change aborts the
 * in-flight request and drops the previous answer instead of showing it as new.
 */
export function useFollowUpSuggestions(
  authScopeKey: string,
  sessionId: string | null,
  messages: readonly Message[],
  enabled: boolean,
): ThreadSuggestion[] {
  const answer = settledFollowUpAnswer(messages);
  const ownerKey = followUpOwnerKey(authScopeKey, sessionId, answer);
  const streamedHereRef = useRef<Set<string>>(new Set());
  const requestedRef = useRef<Set<string>>(new Set());
  const [entry, setEntry] = useState<{
    ownerKey: string;
    suggestions: ThreadSuggestion[];
  } | null>(null);

  // A Run observed streaming here is a completion; a Run restored from history never is.
  for (const message of messages) {
    if (
      authScopeKey &&
      sessionId &&
      message.role === "assistant" &&
      message.isStreaming &&
      message.runId
    ) {
      streamedHereRef.current.add(
        followUpOwnerKey(authScopeKey, sessionId, message)!,
      );
    }
  }

  const runId = answer?.runId ?? null;
  useEffect(() => {
    if (!enabled || !ownerKey || !sessionId || !runId) return undefined;
    if (!streamedHereRef.current.has(ownerKey)) return undefined;
    if (requestedRef.current.has(ownerKey)) return undefined;
    requestedRef.current.add(ownerKey);
    const controller = new AbortController();
    void sessionApi
      .getFollowUpSuggestions(sessionId, runId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        const suggestions = toSuggestions(response.suggestions);
        if (suggestions.length) setEntry({ ownerKey, suggestions });
      })
      .catch(() => {
        // A failed generation leaves the settled answer usable and unlabelled.
      });
    return () => controller.abort();
  }, [enabled, ownerKey, runId, sessionId]);

  return entry && entry.ownerKey === ownerKey ? entry.suggestions : [];
}
