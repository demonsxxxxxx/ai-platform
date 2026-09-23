import { useCallback, useEffect, useState } from "react";
import {
  adminRunsApi,
  type AdminRunDiagnosticAttempt,
  type AdminRunTrajectoryEvent,
  type AdminWorkerExecutionMessage,
} from "../../services/api/adminRuns";

const KIND_LABEL: Record<AdminRunTrajectoryEvent["kind"], string> = {
  message: "消息",
  action: "动作",
  observation: "观察",
  error: "错误",
};

export function AdminRunTrajectoryReplay({
  runId,
  attempts,
  messages,
}: {
  runId: string;
  attempts: AdminRunDiagnosticAttempt[];
  messages: AdminWorkerExecutionMessage[];
}) {
  const [events, setEvents] = useState<AdminRunTrajectoryEvent[]>([]);
  const [position, setPosition] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [omitted, setOmitted] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    setEvents([]);
    setPosition(0);
    setCursor(0);
    setHasMore(false);
    setOmitted(0);
    setPlaying(false);
    setLoading(true);
    setError(false);
    void adminRunsApi.trajectory(runId).then((page) => {
      if (!active) return;
      setEvents(page.events);
      setCursor(page.next_after_sequence);
      setHasMore(page.has_more);
      setOmitted(page.omitted.private + page.omitted.unsupported + page.omitted.invalid);
    }).catch(() => {
      if (active) setError(true);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [runId]);

  const loadMore = useCallback(async () => {
    if (loading) return;
    setLoading(true);
    setError(false);
    try {
      const page = await adminRunsApi.trajectory(runId, cursor);
      setEvents((current) => {
        const seen = new Set(current.map((event) => event.event_id));
        return [...current, ...page.events.filter((event) => !seen.has(event.event_id))];
      });
      setCursor(page.next_after_sequence);
      setHasMore(page.has_more);
      setOmitted((current) => current + page.omitted.private + page.omitted.unsupported + page.omitted.invalid);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [cursor, loading, runId]);

  useEffect(() => {
    if (!playing) return;
    if (position >= events.length) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setPosition((current) => Math.min(current + 1, events.length)), 700);
    return () => window.clearTimeout(timer);
  }, [events.length, playing, position]);

  const current = position > 0 ? events[position - 1] : null;
  const attemptOrdinal = current?.attempt_id
    ? attempts.find((attempt) => attempt.attempt_id === current.attempt_id)?.ordinal
    : null;
  const completedMessage = current?.source_type === "message.completed"
    ? messages.find((message) => message.kind === "answer" && message.sequence === current.sequence)
    : null;

  return (
    <section className="p-4" data-run-trajectory-replay>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">事件回放</h3>
        <span className="text-[11px] text-[var(--theme-text-tertiary)]">只读 · 数据库保存顺序</span>
      </div>
      <p className="mt-1 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
        逐条查看已保存的公开消息、工具动作与结果、阶段观察和错误；回放不会再次执行工具。Agent 完整输出在消息完成时显示，工具观察仅含安全概要；内部失败证据见下方执行诊断。
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <button type="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={position === 0} onClick={() => { setPlaying(false); setPosition((value) => Math.max(0, value - 1)); }}>上一步</button>
        <button type="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={position >= events.length} onClick={() => { setPlaying(false); setPosition((value) => Math.min(value + 1, events.length)); }}>下一步</button>
        <button type="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={!events.length || position >= events.length} onClick={() => setPlaying((value) => !value)}>{playing ? "暂停" : "自动播放"}</button>
        <span className="text-[var(--theme-text-secondary)]">{position} / {events.length} 条</span>
        <button type="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={loading} onClick={() => void loadMore()}>{loading ? "读取中" : hasMore ? "加载后续事件" : "读取新事件"}</button>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs text-[var(--theme-danger)]">事件读取失败；已显示的记录仍可查看。</p> : null}
      {loading && !events.length ? <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">正在读取事件</p> : null}
      {!loading && !events.length && !hasMore ? <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">没有可回放的公开类型化事件</p> : null}
      {omitted > 0 ? <p className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">当前已读取范围有 {omitted} 条私有、旧版或不合规事件未进入回放；不表示运行未发生。</p> : null}
      {current ? (
        <div className="mt-3 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3" data-run-trajectory-current>
          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--theme-text)]">
            <span className="font-semibold">{KIND_LABEL[current.kind]}</span>
            <span>{current.summary ?? current.source_type}</span>
            <span className="ml-auto font-mono text-[11px] text-[var(--theme-text-tertiary)]">#{current.sequence}</span>
          </div>
          <p className="mt-1 text-[11px] text-[var(--theme-text-secondary)]">
            {[attemptOrdinal ? `第 ${attemptOrdinal} 次尝试` : null, current.category, current.stage, current.outcome, typeof current.duration_ms === "number" ? `${current.duration_ms} ms` : null, typeof current.text_length === "number" ? `${current.text_length} 字符` : null].filter(Boolean).join(" · ") || "已记录事件"}
          </p>
          {completedMessage ? (
            <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--theme-text)]" data-run-trajectory-message>
              {completedMessage.text}
            </p>
          ) : null}
          <details className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">
            <summary className="cursor-pointer">技术关联</summary>
            <p className="mt-1 break-all">事件 {current.event_id}</p>
            {current.operation_id ? <p className="break-all">调用 {current.operation_id}</p> : null}
            {current.message_id ? <p className="break-all">消息 {current.message_id}</p> : null}
            {current.attempt_id ? <p className="break-all">Attempt {current.attempt_id}</p> : null}
            {current.causation_event_id ? <p className="break-all">上游事件 {current.causation_event_id}</p> : null}
          </details>
        </div>
      ) : null}
    </section>
  );
}
