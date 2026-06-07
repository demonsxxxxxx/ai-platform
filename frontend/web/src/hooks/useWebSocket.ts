import { useCallback } from "react";

export interface TaskCompleteNotification {
  type: "task:complete";
  data: {
    session_id: string;
    run_id: string;
    status: "completed" | "failed";
    message?: string;
    unread_count?: number;
    project_id?: string | null;
    is_favorite?: boolean;
  };
}

interface UseWebSocketOptions {
  onTaskComplete?: (notification: TaskCompleteNotification) => void;
  enabled?: boolean;
}

export function useWebSocket(options: UseWebSocketOptions = {}) {
  void options;
  const connect = useCallback(async () => {}, []);

  const disconnect = useCallback(() => {
  }, []);

  return {
    isConnected: false,
    connect,
    disconnect,
  };
}
