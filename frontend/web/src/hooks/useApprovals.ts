import { useState, useCallback, useEffect, useRef } from "react";
import type { PendingApproval } from "../types";

interface UseApprovalsOptions {
  sessionId: string | null;
}

export function useApprovals({ sessionId }: UseApprovalsOptions) {
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const hasApprovalsRef = useRef(false);

  const fetchApprovals = useCallback(async () => {
    setApprovals([]);
    hasApprovalsRef.current = false;
  }, []);

  // 添加来自 SSE 的 approval（不再需要轮询来发现）
  const addApproval = useCallback((approval: PendingApproval) => {
    setApprovals((prev) => {
      // 避免重复添加
      if (prev.some((a) => a.id === approval.id)) {
        return prev;
      }
      hasApprovalsRef.current = true;
      return [...prev, approval];
    });
  }, []);

  // 清除所有 approvals（用于对话失败时）
  const clearApprovals = useCallback(() => {
    setApprovals([]);
    hasApprovalsRef.current = false;
  }, []);

  const respondToApproval = useCallback(
    async (
      approvalId: string,
      response: Record<string, unknown>,
      approved: boolean = true,
    ) => {
      void response;
      void approved;
      setIsLoading(true);
      try {
        setApprovals((prev) => prev.filter((a) => a.id !== approvalId));
        return false;
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  // 初始加载时获取一次（用于页面刷新后恢复状态）
  useEffect(() => {
    if (!sessionId) return;
    fetchApprovals();
  }, [fetchApprovals, sessionId]);

  return {
    approvals,
    isLoading,
    respondToApproval,
    addApproval,
    clearApprovals,
    refresh: fetchApprovals,
  };
}
