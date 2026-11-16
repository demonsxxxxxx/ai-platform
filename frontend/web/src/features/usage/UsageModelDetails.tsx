import { useCallback } from "react";
import { Table } from "@lobehub/ui/base-ui";
import { modelUsageApi, type UsageDates } from "../../services/api/modelUsage";
import { useUsageReport } from "./useUsageReport";
import { UsageError, UsageEmpty } from "./UsageReportState";

const tokenColumns = [
  ["input_tokens", "输入"],
  ["cache_read_input_tokens", "缓存读取"],
  ["cache_creation_input_tokens", "缓存创建"],
  ["output_tokens", "输出"],
  ["input_total", "输入合计"],
  ["total_tokens", "总 Token"],
] as const;

export function UsageModelDetails({ userId, dates }: { userId: string; dates: UsageDates }) {
  const load = useCallback((signal: AbortSignal) => modelUsageApi.models(userId, dates, signal), [userId, dates]);
  const { data, loading, error, retry } = useUsageReport(load);
  return <section aria-label="实际模型用量" className="min-w-0 space-y-2 border-t border-[var(--theme-border)] p-3">
    <h3 className="m-0 text-sm font-semibold">实际模型明细</h3>
    {loading && <p role="status">正在加载模型用量…</p>}
    {error !== undefined && <UsageError error={error} retry={retry} />}
    {data && <>
      <div className="min-w-0 max-w-full overflow-x-auto" role="region" aria-label="模型明细横向滚动区域" tabIndex={0}>
        <Table dataSource={data.data} rowKey="model" pagination={false} size="small" tableLayout="fixed" scroll={{ x: 1080 }} styles={{ header: { cell: { padding: "6px 8px", whiteSpace: "nowrap" } }, body: { cell: { padding: "6px 8px", fontVariantNumeric: "tabular-nums" } } }} locale={{ emptyText: <UsageEmpty meta={data.meta} /> }} columns={[
          { key: "model", title: "实际模型", dataIndex: "model", width: 240 },
          ...tokenColumns.map(([key, title]) => ({ key, title, dataIndex: key, width: 140, align: "right" as const, render: (value: number) => value.toLocaleString("zh-CN") })),
        ]} />
      </div>
    </>}
  </section>;
}
