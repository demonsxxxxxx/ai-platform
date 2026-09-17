/**
 * Feedback API - 用户反馈管理
 * 每个用户对每个 run 只能提交一次反馈
 */

import { authFetch } from "./fetch";
import type {
  FeedbackListResponse,
  RatingValue,
} from "../../types/feedback";
import { API_BASE } from "./config";

export const feedbackApi = {
  /**
   * 获取反馈列表
   */
  async list(
    skip: number = 0,
    limit: number = 50,
    rating?: RatingValue,
    userId?: string,
    sessionId?: string,
  ): Promise<FeedbackListResponse> {
    const params = new URLSearchParams({
      skip: skip.toString(),
      limit: limit.toString(),
    });
    if (rating) {
      params.append("rating", rating);
    }
    if (userId) {
      params.append("user_id", userId);
    }
    if (sessionId) {
      params.append("session_id", sessionId);
    }
    return authFetch<FeedbackListResponse>(
      `${API_BASE}/api/feedback/?${params}`,
    );
  },
};
