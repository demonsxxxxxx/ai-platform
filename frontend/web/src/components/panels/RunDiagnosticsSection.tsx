import type { AdminRunDiagnosticsResponse } from "../../services/api/adminRuns";


const COVERAGE_LABELS: Record<string, string> = {
  full: "完整采集",
  partial: "部分采集",
  not_collected: "未采集",
  legacy_record: "历史记录",
  unsupported_schema: "版本暂不支持",
  transport_unavailable: "传输不可用",
};

function diagnosticValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "[无法显示]";
  }
}

function diagnosticJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "[无法显示]";
  }
}

function AttemptStatus({ status }: { status: string }) {
  return (
    <span className="shrink-0 rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
      {status}
    </span>
  );
}

export function RunDiagnosticsSection({
  diagnostics,
  loading,
  error,
}: {
  diagnostics: AdminRunDiagnosticsResponse | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading && !diagnostics) {
    return (
      <section className="p-4" data-run-runtime-diagnostics>
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">执行诊断</h3>
        <p className="mt-2 text-xs text-[var(--theme-text-secondary)]">正在读取诊断记录…</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="p-4" data-run-runtime-diagnostics>
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">执行诊断</h3>
        <p className="mt-2 text-xs text-[var(--theme-danger)]">诊断读取失败：{error}</p>
      </section>
    );
  }
  if (!diagnostics) {
    return (
      <section className="p-4" data-run-runtime-diagnostics>
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">执行诊断</h3>
        <p className="mt-2 text-xs text-[var(--theme-warning)]">诊断响应缺失或格式无效。</p>
      </section>
    );
  }
  const sdkErrors = Array.isArray(diagnostics.details.sdk.errors)
    ? diagnostics.details.sdk.errors
    : [];
  const toolEvidence = [
    ...diagnostics.details.tool_calls,
    ...diagnostics.details.tool_policy_denials,
  ];
  const protocolEvidence = diagnostics.details.executor_protocol;
  return (
    <section className="p-4" data-run-runtime-diagnostics>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--theme-text)]">执行诊断</h3>
        <span className="rounded-md bg-[var(--theme-bg-sidebar)] px-2 py-1 text-[11px] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
          {COVERAGE_LABELS[diagnostics.coverage] ?? diagnostics.coverage}
          {diagnostics.revision ? ` · r${diagnostics.revision}` : ""}
        </span>
      </div>
      {diagnostics.coverage === "not_collected" ? (
        <p className="mt-3 text-xs leading-5 text-[var(--theme-text-secondary)]">
          此 Run 没有可用的私有诊断记录。业务状态仍可从上方状态与时间线判断。
        </p>
      ) : diagnostics.coverage === "unsupported_schema" ? (
        <p className="mt-3 text-xs leading-5 text-[var(--theme-warning)]">
          记录来自当前服务尚未支持的诊断版本，内容未被直接展示。
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          {diagnostics.root ? (
            <div className="border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-xs">
            <p className="font-mono font-semibold text-[var(--theme-danger)]">
              {diagnostics.root.error_code || "未分类异常"}
            </p>
            <p className="mt-1 text-[var(--theme-text-secondary)]">
              {[diagnostics.root.source, diagnostics.root.stage, diagnostics.root.exception_type]
                .filter(Boolean)
                .join(" · ") || "来源信息缺失"}
            </p>
            {diagnostics.root.message ? (
              <p className="mt-2 whitespace-pre-wrap break-words leading-5 text-[var(--theme-text)]">
                {diagnostics.root.message}
              </p>
            ) : null}
            {diagnostics.root.attempt_id ? (
              <p className="mt-2 break-all font-mono text-[11px] text-[var(--theme-text-tertiary)]">
                Attempt {diagnostics.root.attempt_id}
              </p>
            ) : null}
            {diagnostics.root.lease_id || diagnostics.root.callback_id ? (
              <p className="mt-1 break-all font-mono text-[11px] text-[var(--theme-text-tertiary)]">
                {[diagnostics.root.lease_id && `Lease ${diagnostics.root.lease_id}`, diagnostics.root.callback_id && `Callback ${diagnostics.root.callback_id}`]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            ) : null}
            </div>
          ) : (
            <p className="text-xs leading-5 text-[var(--theme-text-secondary)]">
              诊断记录存在，但没有可安全展示的根异常；以下仍保留处理、损失和 Attempt 证据。
            </p>
          )}
          {diagnostics.root?.stack ? (
            <details className="rounded-md border border-[var(--theme-border)] p-3">
              <summary className="cursor-pointer text-xs font-medium text-[var(--theme-text)]">
                异常堆栈
              </summary>
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-[var(--theme-text-secondary)]">
                {diagnostics.root.stack}
              </pre>
            </details>
          ) : null}
          {sdkErrors.length ? (
            <div className="rounded-md border border-[var(--theme-border)] p-3">
              <h4 className="text-xs font-medium text-[var(--theme-text)]">SDK 错误</h4>
              <ul className="mt-2 space-y-1 text-[11px] leading-5 text-[var(--theme-text-secondary)]">
                {sdkErrors.map((item, index) => (
                  <li key={`sdk-error-${index}`} className="break-words font-mono">
                    {diagnosticValue(item)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {protocolEvidence ? (
            <div
              className="rounded-md border border-[var(--theme-border)] p-3"
              data-executor-protocol-evidence
            >
              <h4 className="text-xs font-medium text-[var(--theme-text)]">
                终态协议证据
              </h4>
              <div className="mt-2 space-y-3 text-[11px]">
                <div>
                  <p className="font-medium text-[var(--theme-text-secondary)]">
                    上报结构（已脱敏）
                  </p>
                  <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono leading-5 text-[var(--theme-text-tertiary)]">
                    {diagnosticJson(protocolEvidence.reported)}
                  </pre>
                </div>
                <div className="border-t border-[var(--theme-border)] pt-3">
                  <p className="font-medium text-[var(--theme-text-secondary)]">
                    协议校验
                  </p>
                  {protocolEvidence.validation.length ? (
                    <ul className="mt-1 space-y-1 font-mono leading-5 text-[var(--theme-danger)]">
                      {protocolEvidence.validation.map((item, index) => (
                        <li key={`${item.location}-${item.type}-${index}`} className="break-words">
                          {item.location} · {item.type} · {item.message}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-[var(--theme-text-tertiary)]">
                      无结构化校验明细
                    </p>
                  )}
                  {protocolEvidence.validation_omitted_count ? (
                    <p className="mt-1 text-[var(--theme-warning)]">
                      另省略 {protocolEvidence.validation_omitted_count} 条校验结果
                    </p>
                  ) : null}
                </div>
                <div className="border-t border-[var(--theme-border)] pt-3">
                  <p className="font-medium text-[var(--theme-text-secondary)]">
                    规范化替代结果
                  </p>
                  <pre className="mt-1 overflow-auto whitespace-pre-wrap break-words font-mono leading-5 text-[var(--theme-text-tertiary)]">
                    {diagnosticJson(protocolEvidence.canonical)}
                  </pre>
                </div>
              </div>
            </div>
          ) : null}
          {toolEvidence.length ? (
            <div className="rounded-md border border-[var(--theme-border)] p-3">
              <h4 className="text-xs font-medium text-[var(--theme-text)]">工具证据</h4>
              <ul className="mt-2 space-y-2">
                {toolEvidence.map((item, index) => (
                  <li key={`${diagnosticValue(item.invocation_id)}-${index}`} className="text-[11px]">
                    <p className="font-mono text-[var(--theme-text)]">
                      {diagnosticValue(item.tool_name) || "unknown_tool"}
                      {item.invocation_id ? ` · ${diagnosticValue(item.invocation_id)}` : ""}
                    </p>
                    <p className="mt-0.5 break-words text-[var(--theme-text-secondary)]">
                      {diagnosticValue(
                        item.reason ?? item.last_stage ?? item.state,
                      ) || "未提供结果摘要"}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {diagnostics.handling.length ? (
            <div className="rounded-md border border-[var(--theme-border)] p-3">
              <h4 className="text-xs font-medium text-[var(--theme-text)]">后续处理</h4>
              <ol className="mt-2 space-y-2">
                {diagnostics.handling.map((item, index) => (
                  <li key={`${item.observation_id ?? "handling"}-${index}`} className="text-[11px]">
                    <p className="font-mono text-[var(--theme-text)]">{item.error_code || "handling"}</p>
                    <p className="mt-0.5 text-[var(--theme-text-secondary)]">
                      {[item.source, item.stage, item.exception_type].filter(Boolean).join(" · ")}
                    </p>
                    {item.message ? <p className="mt-1 break-words">{item.message}</p> : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {diagnostics.losses.length ? (
            <div className="rounded-md bg-[var(--theme-warning-soft)] p-3 text-[11px] text-[var(--theme-warning)]">
              <p className="font-medium">有 {diagnostics.losses.length} 项内容经过裁剪或拒绝</p>
              <ul className="mt-1 space-y-0.5 font-mono">
                {diagnostics.losses.slice(0, 8).map((item, index) => (
                  <li key={`${item.field}-${item.reason}-${index}`}>{item.field} · {item.reason}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {diagnostics.attempts.length ? (
            <div className="rounded-md border border-[var(--theme-border)] p-3">
              <h4 className="text-xs font-medium text-[var(--theme-text)]">执行尝试</h4>
              <div className="mt-2 space-y-2">
                {diagnostics.attempts.map((attempt) => (
                  <div key={attempt.attempt_id} className="flex items-start justify-between gap-3 text-[11px]">
                    <div className="min-w-0">
                      <p className="break-all font-mono text-[var(--theme-text)]">
                        #{attempt.ordinal} · {attempt.attempt_id}
                      </p>
                      <p className="mt-0.5 text-[var(--theme-text-tertiary)]">
                        {attempt.owner_kind} · {attempt.terminal_reason || "处理中"}
                      </p>
                    </div>
                    <AttemptStatus status={attempt.status} />
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <div className="rounded-md bg-[var(--theme-bg-sidebar)] px-3 py-2 text-[11px] text-[var(--theme-text-tertiary)]">
            保留 {diagnostics.counts.retained_observations} 条观测
            {diagnostics.counts.omitted_observations
              ? `，省略 ${diagnostics.counts.omitted_observations} 条`
              : ""}
            {diagnostics.versions.redaction_policy
              ? ` · 脱敏 ${diagnostics.versions.redaction_policy}`
              : ""}
            {diagnostics.versions.budget_policy
              ? ` · 预算 ${diagnostics.versions.budget_policy}`
              : ""}
          </div>
        </div>
      )}
    </section>
  );
}
