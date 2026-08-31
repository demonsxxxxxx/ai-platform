import { useCallback, useEffect, useRef, useState } from "react";
import { LoaderCircle, WifiOff } from "lucide-react";

import type { VisibleConnectionStatus } from "./sessionState";

interface ReconnectAttempt {
  owner: string;
  token: number;
}

interface ChatConnectionStatusProps {
  status: VisibleConnectionStatus;
  owner: string;
  label: string;
  reconnectLabel: string;
  reconnectingLabel: string;
  onReconnect: () => Promise<unknown>;
}

export function ChatConnectionStatus({
  status,
  owner,
  label,
  reconnectLabel,
  reconnectingLabel,
  onReconnect,
}: ChatConnectionStatusProps) {
  const nextAttemptTokenRef = useRef(0);
  const [reconnectAttempt, setReconnectAttempt] =
    useState<ReconnectAttempt | null>(null);
  const isReconnectPending =
    status === "disconnected" && reconnectAttempt?.owner === owner;

  useEffect(() => {
    setReconnectAttempt((current) =>
      current?.owner === owner && status === "disconnected" ? current : null,
    );
  }, [owner, status]);

  const handleReconnect = useCallback(async () => {
    if (status !== "disconnected" || isReconnectPending) return;

    const attempt = {
      owner,
      token: ++nextAttemptTokenRef.current,
    };
    setReconnectAttempt(attempt);
    try {
      await onReconnect();
    } finally {
      setReconnectAttempt((current) =>
        current?.owner === attempt.owner && current.token === attempt.token
          ? null
          : current,
      );
    }
  }, [isReconnectPending, onReconnect, owner, status]);

  return (
    <div className="mx-auto mb-2 max-w-4xl px-2">
      <div
        aria-atomic="true"
        aria-busy={status !== "disconnected" || isReconnectPending}
        aria-live="polite"
        className={`flex items-center gap-2.5 rounded-xl border px-3 py-2 text-sm shadow-sm ${
          status === "disconnected"
            ? "border-amber-300/70 bg-amber-50 text-amber-950 dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-amber-100"
            : "border-sky-300/70 bg-sky-50 text-sky-950 dark:border-sky-700/70 dark:bg-sky-950/30 dark:text-sky-100"
        }`}
        data-chat-connection-status={status}
        role="status"
      >
        {status === "disconnected" ? (
          <WifiOff aria-hidden="true" className="h-4 w-4 shrink-0" />
        ) : (
          <LoaderCircle
            aria-hidden="true"
            className="h-4 w-4 shrink-0 animate-spin"
          />
        )}
        <span className="min-w-0 flex-1">{label}</span>
        {status === "disconnected" && (
          <button
            type="button"
            className="shrink-0 rounded-lg border border-current/25 bg-white/70 px-2.5 py-1 text-xs font-medium transition-colors hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:cursor-wait disabled:opacity-60 dark:bg-stone-900/60 dark:hover:bg-stone-900"
            disabled={isReconnectPending}
            onClick={() => void handleReconnect()}
          >
            {isReconnectPending ? reconnectingLabel : reconnectLabel}
          </button>
        )}
      </div>
    </div>
  );
}
