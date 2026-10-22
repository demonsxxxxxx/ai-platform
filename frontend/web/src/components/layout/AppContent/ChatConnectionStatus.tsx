import { ConnectionState } from "../../chat/assistant-ui/elements/connection-state";
import { useCallback, useEffect, useRef, useState } from "react";

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
    <div className="mx-auto mb-2 max-w-[60rem] px-2">
      <ConnectionState status={status} disconnected={status === "disconnected"} pending={isReconnectPending}
        label={label} retryLabel={isReconnectPending ? reconnectingLabel : reconnectLabel}
        onRetry={() => void handleReconnect()} />
    </div>
  );
}
