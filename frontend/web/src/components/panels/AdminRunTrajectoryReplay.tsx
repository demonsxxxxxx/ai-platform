import { Button, Input } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
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
  attemptId = "all",
  selectedSequence,
  initialAfterSequence = 0,
  active = false,
  onSelectSequence,
}: {
  runId: string;
  attempts: AdminRunDiagnosticAttempt[];
  messages: AdminWorkerExecutionMessage[];
  attemptId?: string;
  selectedSequence?: number;
  initialAfterSequence?: number;
  active?: boolean;
  onSelectSequence?: (sequence: number) => void;
}) {
  const [events, setEvents] = useState<AdminRunTrajectoryEvent[]>([]);
  const [position, setPosition] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [omitted, setOmitted] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [query, setQuery] = useState("");
  const [following, setFollowing] = useState(false);
  const [newEvents, setNewEvents] = useState(0);
  const tail = useRef<HTMLLIElement>(null);
  const [kind, setKind] = useState("all");
  const owner = useRef(0);
  const locatedSequence = useRef<number | null>(null);
  const activeRun = useRef(runId);
  activeRun.current = runId;
  const filtered = useMemo(() => events.filter((event) =>
    (attemptId === "all" || event.attempt_id === attemptId) && (kind === "all" || event.kind === kind) &&
    (!query.trim() || [event.summary, event.source_type, event.outcome, event.stage, event.sequence, event.operation_id].join(" ").toLowerCase().includes(query.trim().toLowerCase()))), [attemptId, events, kind, query]);

  useEffect(() => {
    let active = true;
    owner.current += 1;
    setEvents([]);
    setFollowing(false);
    setNewEvents(0);
    setPosition(0);
    setCursor(0);
    setHasMore(false);
    setOmitted(0);
    setPlaying(false);
    setLoading(true);
    setError(false);
    void adminRunsApi.trajectory(runId, initialAfterSequence).then((page) => {
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
    return () => { active = false; owner.current += 1; };
  }, [runId, initialAfterSequence]);

  const loadMore = useCallback(async () => {
    if (loading) return;
    setLoading(true);
    setError(false);
    const generation = owner.current;
    const current = () => generation === owner.current && activeRun.current === runId;
    try {
      const page = await adminRunsApi.trajectory(runId, cursor);
      if (!current()) return;
      if (page.events.length) setNewEvents((value) => value + page.events.length);
      setEvents((current) => {
        const seen = new Set(current.map((event) => event.event_id));
        return [...current, ...page.events.filter((event) => !seen.has(event.event_id))];
      });
      setCursor(page.next_after_sequence);
      setHasMore(page.has_more);
      setOmitted((current) => current + page.omitted.private + page.omitted.unsupported + page.omitted.invalid);
    } catch {
      if (current()) setError(true);
    } finally {
      if (current()) setLoading(false);
    }
  }, [cursor, loading, runId]);

  useEffect(() => {
    if (active && following && hasMore && !loading && !error) void loadMore();
  }, [active, following, hasMore, loading, error, loadMore]);
  useEffect(() => {
    if (!active || hasMore) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void loadMore(); }, 5000);
    return () => window.clearInterval(timer);
  }, [active, hasMore, loadMore]);
  useEffect(() => {
    if (following && active) { setPosition(events.length); tail.current?.scrollIntoView({ block: "nearest" }); setNewEvents(0); }
  }, [active, events.length, following]);

  useEffect(() => {
    if (!playing) return;
    if (position >= events.length) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => setPosition((current) => Math.min(current + 1, events.length)), 700);
    return () => window.clearTimeout(timer);
  }, [events.length, playing, position]);

  useEffect(() => {
    if (selectedSequence == null) { locatedSequence.current = null; return; }
    if (locatedSequence.current === selectedSequence) return;
    setFollowing(false);
    setPlaying(false);
    const index = events.findIndex((event) => event.sequence === selectedSequence);
    if (index >= 0) { setPosition(index + 1); locatedSequence.current = selectedSequence; }
  }, [events, selectedSequence]);
  const current = position > 0 ? events[position - 1] : null;
  const choosePosition = (value: number) => { setFollowing(false); setPlaying(false); setPosition(value); if (events[value - 1]) onSelectSequence?.(events[value - 1].sequence); };
  const attemptOrdinal = current?.attempt_id
    ? attempts.find((attempt) => attempt.attempt_id === current.attempt_id)?.ordinal
    : null;
  const related = current ? events.filter((event) => event.attempt_id === current.attempt_id && (event.causation_event_id === current.event_id || current.causation_event_id === event.event_id || (current.subagent_id != null && current.subagent_id === event.subagent_id))) : [];
  const completedMessage = current?.source_type === "message.completed"
    ? messages.find((message) => message.kind === "answer" && message.sequence === current.sequence)
    : null;

  return (
    <section className="p-4" data-run-trajectory-replay onWheel={(event) => { if (event.deltaY < 0) setFollowing(false); }} onKeyDown={(event) => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) setFollowing(false); }}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">事件回放</h3>
        <span className="text-[11px] text-[var(--theme-text-tertiary)]">只读 · 数据库保存顺序</span>
      </div>
      <p className="mt-1 text-[11px] leading-5 text-[var(--theme-text-tertiary)]">
        逐条查看已保存的公开消息、工具动作与结果、阶段观察和错误；回放不会再次执行工具。Agent 完整输出在消息完成时显示，工具观察仅含安全概要；内部失败证据见下方执行诊断。
      </p>
      <div className="run-event-tools"><Input aria-label="搜索已加载事件" placeholder="搜索已加载事件、序号或调用" value={query} onChange={(event) => setQuery(event.target.value)} /><LobeSelectField aria-label="事件类型" value={kind} onChange={setKind} options={[{ value: "all", label: "全部类型" }, ...Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))]} /></div>
      <p className="mt-2 text-xs text-[var(--theme-text-secondary)]">仅搜索已加载的安全事件 · 已加载 {events.length} 条 · 匹配 {filtered.length} 条{hasMore ? " · 还有未读取内容" : ""}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <Button type="text" htmlType="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={position === 0} onClick={() => choosePosition(Math.max(0, position - 1))}>上一步</Button>
        <Button type="text" htmlType="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={position >= events.length} onClick={() => choosePosition(Math.min(position + 1, events.length))}>下一步</Button>
        <Button type="text" htmlType="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={!events.length || position >= events.length} onClick={() => setPlaying((value) => !value)}>{playing ? "暂停" : "自动播放"}</Button>
        <span className="text-[var(--theme-text-secondary)]">{position} / {events.length} 条</span>
        {active ? <Button type="text" aria-pressed={following} onClick={() => setFollowing((value) => !value)}>{following ? "暂停跟随最新" : "跟随最新"}</Button> : null}
        {!following && newEvents > 0 ? <Button type="text" onClick={() => { setNewEvents(0); setPosition(events.length); tail.current?.scrollIntoView({ block: "nearest" }); }}>有 {newEvents} 条新加载事件</Button> : null}
        <Button type="text" htmlType="button" className="rounded-md border border-[var(--theme-border)] px-2.5 py-1.5 disabled:opacity-40" disabled={loading} onClick={() => void loadMore()}>{loading ? "读取中" : hasMore ? "加载后续事件" : "读取新事件"}</Button>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs text-[var(--theme-danger-text)]">事件读取失败；已显示的记录仍可查看。</p> : null}
      {loading && !events.length ? <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">正在读取事件</p> : null}
      {!loading && !events.length && !hasMore ? <p className="mt-2 text-xs text-[var(--theme-text-tertiary)]">没有可回放的公开类型化事件</p> : null}
      {omitted > 0 ? <p className="mt-2 text-[11px] text-[var(--theme-text-tertiary)]">当前已读取范围有 {omitted} 条私有、旧版或不合规事件未进入回放；不表示运行未发生。</p> : null}
      {selectedSequence && !events.some((event) => event.sequence === selectedSequence) ? <p role="status" className="mt-2 text-xs">指定事件尚未加载或不可披露，请读取后续事件；不表示该事件未发生。</p> : null}
      {filtered.length ? <ol className="run-event-list" aria-label="已加载公开事件">{filtered.map((event) => <li className="run-event-row" ref={event === filtered.at(-1) ? tail : undefined} key={event.event_id}><Button type="text" htmlType="button" aria-pressed={current?.event_id === event.event_id} onClick={() => choosePosition(events.indexOf(event) + 1)}><span className="text-sm">#{event.sequence} · {KIND_LABEL[event.kind]} · {event.summary || event.source_type}{event.outcome ? ` · ${event.outcome}` : ""}</span></Button></li>)}</ol> : !loading ? <p className="mt-2 text-sm">当前已加载范围没有匹配事件。</p> : null}
      {current && (attemptId === "all" || current.attempt_id === attemptId) ? (
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
          {related.length ? <div className="mt-3"><h4 className="text-sm font-medium">已绑定的上游与后续事件</h4>{related.map((event) => <Button key={event.event_id} type="text" size="small" onClick={() => choosePosition(events.indexOf(event) + 1)}>#{event.sequence} · {event.summary || event.source_type}</Button>)}</div> : null}
          {current.subagent_id ? <p className="mt-2 break-all text-xs">子代理 {current.subagent_id} · 只按真实事件身份关联</p> : null}
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
