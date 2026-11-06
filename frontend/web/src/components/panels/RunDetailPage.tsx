import { Button, Tabs } from "@lobehub/ui/base-ui";
import { ArrowLeft, Download, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CopyRunValue, IdentityField, RuntimeHealth, StatusBadge } from "./RunDetailFacts";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useRunDetail } from "./useRunDetail";
import { readRunDetailTab, runListSearch, RUN_DETAIL_TABS } from "./runDetailNavigation";
import { agentDisplayName, dateTime, estimatedCostLabel, runDisplayTitle, tokenCountLabel, userDisplayName, workspaceDisplayName } from "./runPresentation";
import { RunArtifactList } from "./RunArtifactList";
import { RunEvidenceSearch } from "./RunEvidenceSearch";
import { RunComparison } from "./RunComparison";
import { RunModelCalls } from "./RunModelCalls";
import { RunDiagnosticsSection } from "./RunDiagnosticsSection";
import { RunInvestigationTimeline } from "./RunInvestigationTimeline";
import { AdminRunTrajectoryReplay } from "./AdminRunTrajectoryReplay";
import { PanelLoadingState } from "../common/PanelLoadingState";
import { LobeSelectField } from "../lobe-ui/LobeSelectField";
import { formatRunDuration, RUN_LATENCY_LABELS } from "./runPerformance";
import { MarkdownRenderer } from "../chat/ChatMessage/MarkdownRenderer";
import type { AdminRunDiagnosticsResponse } from "../../services/api/adminRuns";

const COVERAGE: Record<string, string> = { full: "诊断已采集", partial: "诊断部分采集", not_collected: "诊断未采集", legacy_record: "历史诊断", unsupported_schema: "诊断版本不支持", transport_unavailable: "诊断传输不可用" };
function forAttempt(diagnostics: AdminRunDiagnosticsResponse | null, attempt: string) {
  if (!diagnostics || attempt === "all") return diagnostics;
  return { ...diagnostics, root: diagnostics.root?.attempt_id === attempt ? diagnostics.root : null,
    handling: diagnostics.handling.filter((item) => item.attempt_id === attempt),
    details: { ...diagnostics.details, sdk: {}, projection_failure: undefined, executor_protocol: undefined,
      tool_lifecycles: [], tool_calls: [], tool_policy_denials: [],
      observations: diagnostics.details.observations?.filter((item) => item.attempt_id === attempt) },
  };
}

export function RunDetailPage({ runId }: { runId: string }) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const tab = readRunDetailTab(location.search);
  const attempt = params.get("attempt") || "all";
  const state = useRunDetail(runId);
  const { detail, diagnostics, investigation, inspection } = state;
  const data = detail.data;
  const title = useRef<HTMLHeadingElement>(null);
  const entered = useRef(false);
  useEffect(() => {
    if (data && !entered.current) { entered.current = true; title.current?.focus({ preventScroll: true }); }
  }, [data]);
  const [completeAnswer, setCompleteAnswer] = useState(false);
  const [initialEventCursor] = useState(() => Math.max(0, (Number(params.get("event")) || 0) - 10));
  const changeTab = (value: string) => setParams((current) => { if (value === "overview") current.delete("tab"); else current.set("tab", value); return current; });
  const changeAttempt = (value: string) => setParams((current) => { if (value === "all") current.delete("attempt"); else current.set("attempt", value); current.delete("stage"); current.delete("event"); current.delete("observation"); current.delete("after"); return current; });
  const selectedDiagnostics = forAttempt(diagnostics.data, attempt);
  const selectedInvestigation = investigation.data && attempt !== "all" ? { ...investigation.data, stages: investigation.data.stages.filter((stage) => stage.attempt_id === attempt) } : investigation.data;
  const execution = data?.worker_execution;
  const response = execution?.response || "";
  const stage = params.get("stage");
  const openEvidence = () => {
    const root = diagnostics.data?.root;
    setParams((current) => { current.set("tab", "execution"); if (root?.attempt_id) current.set("attempt", root.attempt_id); if (root?.observation_id) current.set("observation", root.observation_id); return current; });
  };
  const tabs = RUN_DETAIL_TABS.map((item) => ({ key: item.key, label: item.label }));
  const status = data?.run.status;
  const action = investigation.data?.stages.findLast((item) => item.status === "running" || item.status === "waiting")?.label || (status === "queued" ? "等待 Worker 接单" : status === "cancel_requested" ? "正在取消" : status === "cancelled" ? "运行已取消" : "正在执行");
  const firstProblem = diagnostics.data?.root;
  const ended = status && ["succeeded", "failed", "cancelled"].includes(status);
  const nextAction = firstProblem ? "查看异常证据" : response || data?.artifacts.length ? "查看结果" : "查看执行过程";
  return <div className="run-detail-page" data-run-monitor-detail data-run-detail-page>
    <div className="run-detail-navigation"><Link to={`/runs${runListSearch(location.search)}`} className="inline-flex items-center gap-2"><ArrowLeft size={16} />运行监控</Link>
      <div className="flex flex-wrap items-center gap-2"><CopyRunValue label="详情链接" value={window.location.href} /><Button type="text" htmlType="button" size="small" icon={<RefreshCw size={15} />} onClick={state.refresh}>刷新</Button><Button type="default" htmlType="button" icon={<Download size={15} />} loading={state.exporting} disabled={state.exporting} onClick={() => void state.exportDiagnostics()}>下载脱敏诊断包</Button></div>
    </div>
    {detail.loading && !data ? <PanelLoadingState text="正在读取运行详情" /> : null}
    {detail.error ? <p role="alert" className="run-detail-error">{data ? "刷新失败，仍显示上次记录：" : ""}{detail.error}<Button type="text" htmlType="button" onClick={() => void detail.reload()}>重新读取详情</Button></p> : null}
    {state.exportError ? <p role="alert" className="run-detail-error">{state.exportError}</p> : null}
    {data ? <>
      <header className="run-detail-summary" data-worker-execution-result><div className="flex flex-wrap items-center gap-3"><h1 ref={title} tabIndex={-1}>{runDisplayTitle(data.run)}</h1><StatusBadge status={status} /></div>
        <p className="mt-3 font-medium">{status === "failed" ? "运行失败" : status === "succeeded" ? response ? "运行正常结束，已有公开回答" : "运行正常结束，未记录公开回答" : action}</p>
        {firstProblem ? <p className="mt-2 text-sm text-[var(--theme-danger-text)]">最早留存异常 · {firstProblem.stage || "阶段未记录"}：{firstProblem.message || firstProblem.error_code}</p> : null}
        <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">{userDisplayName(data.run)} · {workspaceDisplayName(data.run)} · {agentDisplayName(data.run)}</p>
        <div className="run-detail-facts"><span>开始 {dateTime(data.run.started_at)}</span><span>{ended ? "结束" : "最近读取"} {dateTime(ended ? data.run.finished_at : detail.updatedAt)}</span><span>{ended ? "总耗时" : "已运行"} {formatRunDuration(ended ? investigation.data?.total_ms : investigation.data?.elapsed_ms)}</span><span>{COVERAGE[diagnostics.data?.coverage ?? ""] || "诊断待读取"}</span></div>
        <div className="mt-3 flex flex-wrap items-center gap-3"><Button type="primary" htmlType="button" onClick={() => firstProblem ? openEvidence() : changeTab(response || data.artifacts.length ? "results" : "execution")}>{nextAction}</Button><span className="text-xs text-[var(--theme-text-secondary)]">{data.artifacts.length} 个已登记产物 · {dateTime(detail.updatedAt)}</span></div>
      </header>
      <div className="run-detail-tabs"><Tabs activeKey={tab} onChange={changeTab} items={tabs} /></div>
      <div role="tabpanel" aria-label={RUN_DETAIL_TABS.find((item) => item.key === tab)?.label} className="run-detail-content">
        {tab === "overview" ? <>
          <section className="run-detail-section"><h2>运行概况</h2><div className="run-detail-metrics">{(Object.keys(RUN_LATENCY_LABELS) as Array<keyof typeof RUN_LATENCY_LABELS>).map((name) => <div key={name} data-run-latency={name}><span>{RUN_LATENCY_LABELS[name]}</span><strong>{formatRunDuration(investigation.data?.[name])}</strong></div>)}</div><p className="run-detail-note">排队与执行按平台记录测量；首段正文不是供应商 TTFT。SDK 与工具区间可能重叠，不能相加。</p></section>
          <RuntimeHealth detail={data} />
          {data.evidence_coverage?.truncated ? <p role="status" className="run-detail-note">首屏回答源事件达到读取上限（{data.evidence_coverage.event_limit} 条 / {data.evidence_coverage.payload_budget_bytes} 字节），回答可能不完整。执行事件可按序号分页查看。</p> : null}
          {data.evidence_coverage?.rejected_attempt_events ? <p role="status" className="run-detail-note">有 {data.evidence_coverage.rejected_attempt_events} 条源事件未能绑定本 Run 的执行尝试，未用于结果投影。</p> : null}
          <section className="run-detail-section"><h2>{firstProblem ? "问题与下一步" : "结果摘要"}</h2><p className="mt-2 whitespace-pre-wrap break-words">{firstProblem ? firstProblem.message || firstProblem.error_code : response.slice(0, 400) || "未记录公开回答，可查看执行过程与已登记产物。"}</p>{data.run.error_code ? <p className="mt-2 text-sm">公共终态分类：{data.run.error_code}</p> : null}<Button type="text" htmlType="button" onClick={() => firstProblem ? openEvidence() : changeTab("results")}>{firstProblem ? "查看相关证据与处理过程" : "打开结果与产物"}</Button></section>
          <section className="run-detail-section"><h2>关联对象</h2><dl className="run-detail-identities"><IdentityField label="Run ID" value={runId} /><IdentityField label="Session ID" value={data.run.session_id} /><IdentityField label={data.run.trace_id_recorded ? "已记录 Trace ID" : "派生关联 ID"} value={data.run.trace_id} /></dl></section>
        </> : null}
        <div hidden={tab !== "execution"}>
          <div className="run-detail-section"><LobeSelectField aria-label="执行尝试范围" value={attempt} options={[{ value: "all", label: "全部执行尝试" }, ...(diagnostics.data?.attempts ?? []).map((item) => ({ value: item.attempt_id, label: `第 ${item.ordinal} 次尝试 · ${item.status}` }))]} onChange={changeAttempt} /></div>
          <RunInvestigationTimeline investigation={selectedInvestigation} loading={investigation.loading} error={investigation.error} diagnostics={selectedDiagnostics} onRetry={() => void investigation.reload()} selectedStageId={stage} onSelectStage={(id) => setParams((current) => { current.set("stage", id); return current; })} />
          <RunModelCalls inspection={inspection.data} attemptId={attempt} onSelectOperation={(id, owner) => setParams((current) => { if (owner) current.set("attempt", owner); current.set("stage", `tool:${owner || "unknown"}:${id}`); return current; })} />
          <RunEvidenceSearch runId={runId} attemptId={attempt} onSelect={(sequence, observation, after) => setParams((current) => { current.delete("event"); current.delete("observation"); if (sequence) current.set("event", String(sequence)); if (observation) current.set("observation", observation); if (after != null) current.set("after", String(after)); return current; })} />
          <AdminRunTrajectoryReplay active={tab === "execution" && !ended} initialAfterSequence={Math.max(0, Number(params.get("after")) || initialEventCursor)} runId={runId} attempts={diagnostics.data?.attempts ?? []} messages={execution?.messages ?? []} attemptId={attempt} selectedSequence={Number(params.get("event")) || undefined} onSelectSequence={(sequence) => setParams((current) => { current.set("event", String(sequence)); return current; }, { replace: true })} />
          <RunDiagnosticsSection diagnostics={selectedDiagnostics} loading={diagnostics.loading} error={diagnostics.error} selectedObservationId={params.get("observation")} onRetry={() => void diagnostics.reload()} />
        </div>
        {tab === "results" ? <>
          <section className="run-detail-section" data-worker-execution-content><div className="flex flex-wrap items-center justify-between gap-2"><h2>公开回答</h2><CopyRunValue label="公开回答" value={response} /></div><p className="run-detail-note">{execution?.answer_projection ? { available: "已有可验证回答", incomplete: "回答账本尚不完整", invalid: "回答账本未通过校验", unknown: "未采集或证据不足" }[execution.answer_projection.status] : "回答完整性未记录"}</p>
            {response ? <div data-run-agent-output><MarkdownRenderer content={completeAnswer ? response : response.slice(0, 12000)} />{response.length > 12000 && !completeAnswer ? <Button type="default" htmlType="button" onClick={() => setCompleteAnswer(true)}>展开已保留的完整回答（{response.length} 字符）</Button> : null}</div> : <p>未记录公开回答。运行状态和已登记产物独立展示。</p>}
          </section>
          <RunArtifactList runId={runId} artifacts={data.artifacts} />
        </> : null}
        {tab === "configuration" ? <>
          <section className="run-detail-section"><h2>执行时配置</h2><dl className="run-detail-identities"><IdentityField label="模型" value={inspection.data?.configuration.model_value || data.run.model_value} /><IdentityField label="模型配置版本" value={(inspection.data?.configuration.model_gateway_revision ?? data.run.model_gateway_revision) == null ? null : String(inspection.data?.configuration.model_gateway_revision ?? data.run.model_gateway_revision)} /><IdentityField label="Run 契约" value={data.run.contract_version} /><IdentityField label="执行契约" value={data.run.executor_schema_version} /></dl><p className="run-detail-note">展示已记录的执行事实，不以当前配置倒填历史。原始提示词和私有执行载荷不在此页开放。</p></section>
          <section className="run-detail-section"><h2>Token 与估算成本</h2><p>输入 {tokenCountLabel(data.run.input_token_count)} · 输出 {tokenCountLabel(data.run.output_token_count)} · 总计 {tokenCountLabel(data.run.total_token_count)}</p><p className="mt-2">估算成本 {estimatedCostLabel(data.run.estimated_cost_minor)}</p><p className="run-detail-note">未采集的 cache、成本或版本不记为 0。</p></section>
          <section className="run-detail-section"><h2>输入与版本快照</h2>{inspection.error ? <p role="alert">{inspection.error}<Button type="text" htmlType="button" onClick={() => void inspection.reload()}>重试配置查询</Button></p> : null}{inspection.loading && !inspection.data ? <PanelLoadingState text="正在读取配置快照" /> : null}{inspection.data ? <><dl className="run-detail-identities"><IdentityField label="Profile revision" value={inspection.data.configuration.profile_revision == null ? null : String(inspection.data.configuration.profile_revision)} /><IdentityField label="Profile hash" value={inspection.data.configuration.profile_hash} /><IdentityField label="上下文压缩窗口" value={inspection.data.configuration.auto_compact_window == null ? null : String(inspection.data.configuration.auto_compact_window)} /><IdentityField label="最大输出 Token" value={inspection.data.configuration.max_output_tokens == null ? null : String(inspection.data.configuration.max_output_tokens)} /></dl><h3 className="mt-4 font-medium">允许查看的输入字段</h3>{Object.keys(inspection.data.input.fields).length ? <dl>{Object.entries(inspection.data.input.fields).map(([name, value]) => <div key={name} className="flex flex-wrap gap-3 py-1"><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl> : <p className="run-detail-note">没有可披露的已记录输入参数。</p>}<h3 className="mt-4 font-medium">执行时附件引用</h3>{inspection.data.input.attachments?.length ? <ul>{inspection.data.input.attachments.map((file, index) => <li key={file.file_id || index} className="mt-2 break-words">{file.name || "名称未记录"} · {file.content_type} · {file.size_bytes ?? "未知"} 字节 · {file.availability === "active" ? "登记有效，可用性未核验" : "不可用"}</li>)}</ul> : <p className="run-detail-note">没有可披露的已绑定附件引用。</p>}{inspection.data.input.attachments_truncated ? <p>仅显示前 100 个附件引用。</p> : null}<h3 className="mt-4 font-medium">冻结 Skill 版本</h3>{inspection.data.configuration.skills.length ? <ul>{inspection.data.configuration.skills.map((skill, index) => <li className="mt-2 break-all text-sm" key={index}>{skill.skill_id || "身份未记录"} · {skill.version || "版本未记录"}<details className="text-xs"><summary>内容版本</summary>{skill.content_hash || "未记录"}</details></li>)}</ul> : <p className="run-detail-note">没有已记录的 Skill 快照。</p>}{inspection.data.configuration.skills_truncated ? <p>仅保留前 100 个 Skill 快照。</p> : null}</> : null}</section>
        </> : null}
        {tab === "history" ? <><RunComparison key={runId} runId={runId} /><section className="run-detail-section"><h2>执行尝试</h2><p className="run-detail-note">平台执行尝试与模型内部重试、创建新 Run 的重试/续跑分开记录。</p>{diagnostics.loading && !diagnostics.data ? <PanelLoadingState text="正在读取尝试记录" /> : null}{diagnostics.error ? <p role="alert">{diagnostics.error}</p> : null}<ol className="space-y-3">{diagnostics.data?.attempts.map((item) => <li key={item.attempt_id} className="border-l-2 border-[var(--theme-border)] pl-3"><div className="flex flex-wrap items-center gap-2"><strong>第 {item.ordinal} 次尝试</strong><StatusBadge status={item.status} /></div><p className="mt-1 text-sm">{dateTime(item.started_at)} → {dateTime(item.finished_at)}</p><p className="mt-1 text-xs">{item.terminal_reason || item.error_code || "未记录终止原因"}</p><Button type="text" htmlType="button" onClick={() => { setParams((current) => { current.set("attempt", item.attempt_id); current.set("tab", "execution"); current.delete("stage"); current.delete("event"); return current; }); }}>查看此次尝试证据</Button></li>)}</ol>{!diagnostics.loading && !diagnostics.data?.attempts.length ? <p>没有已记录的执行尝试。</p> : null}<h2 className="mt-6">来源与后续运行</h2>{inspection.error ? <p role="alert">{inspection.error}<Button type="text" htmlType="button" onClick={() => void inspection.reload()}>重试历史查询</Button></p> : null}{inspection.data?.trigger.kind ? <p>触发来源：{{retry:"重试",resume:"续跑",copy:"复制",chat_api:"聊天提交入口",agent_profile_api:"Agent API",direct_skill:"直接 Skill API",direct_api:"直接 Run API"}[inspection.data.trigger.kind] || inspection.data.trigger.kind}</p> : <p>触发渠道未采集。</p>}{inspection.data?.history.relationships.map((item) => <div className="mt-3 border-l-2 border-[var(--theme-border)] pl-3" key={item.child_run_id}><p>{{retry:"重新执行",resume:"续跑",copy:"复制",unknown:"动作未记录"}[item.action]} · <StatusBadge status={item.status} /></p><p className="text-xs">{dateTime(item.created_at)}</p><Link className="underline" to={`/runs/${encodeURIComponent(item.child_run_id === runId ? item.source_run_id : item.child_run_id)}?tab=history`}>{item.child_run_id === runId ? "查看来源 Run" : "查看后续 Run"}</Link></div>)}{!inspection.loading && !inspection.data?.history.relationships.length ? <p className="mt-2">没有已记录的 Run 操作关系。</p> : null}{inspection.data?.history.truncated ? <p>仅显示最近 50 条关系。</p> : null}</section></> : null}
      </div>
    </> : null}
  </div>;
}
