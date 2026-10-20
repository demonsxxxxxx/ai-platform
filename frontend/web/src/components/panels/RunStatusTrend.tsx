import type { AdminRunMonitorResponse } from "../../services/api/adminRuns";

const SERIES = [
  { key: "succeeded", label: "成功", color: "var(--theme-success)" },
  { key: "failed", label: "失败", color: "var(--theme-danger)" },
  { key: "running", label: "执行中", color: "var(--theme-info)" },
] as const;

export function RunStatusTrend({ report }: { report: AdminRunMonitorResponse }) {
  const points = report.trend ?? [];
  const max = Math.max(2, ...points.flatMap((point) => SERIES.map(({ key }) => point[key])));
  const ceiling = Math.ceil(max / 2) * 2;
  const x = (index: number) => 30 + index * 360 / Math.max(1, points.length - 1);
  const y = (value: number) => 116 - value * 96 / ceiling;
  const dateFormat = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    ...(Date.parse(report.window.to_at) - Date.parse(report.window.from_at) > 86_400_000
      ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const time = (value: string) => dateFormat.format(new Date(value));
  return (
    <section className="run-overview-card run-trend" aria-label="运行趋势" data-run-status-trend>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs font-semibold">运行趋势</h2>
        <div className="flex gap-3 text-[11px] text-[var(--theme-text-secondary)]">
          {SERIES.map(({ key, label, color }) => <span key={key} className="inline-flex items-center gap-1.5"><span className="size-1.5 rounded-full" style={{ background: color }} />{label}</span>)}
        </div>
      </div>
      {points.length ? <>
        <svg viewBox="0 0 410 144" role="img" aria-label="按提交时间分组的任务当前状态数量" className="mt-2 w-full flex-1" preserveAspectRatio="none">
          <title>当前筛选时间窗的运行趋势，北京时间；每个任务只计一次</title>
          {[0, ceiling / 2, ceiling].map((value) => <g key={value}>
            <line x1="30" x2="390" y1={y(value)} y2={y(value)} stroke="var(--theme-border)" strokeDasharray="3 4" />
            <text x="20" y={y(value) + 4} textAnchor="end" fill="var(--theme-text-tertiary)" fontSize="10">{value}</text>
          </g>)}
          {SERIES.map(({ key, color, label }) => <g key={key}>
            <polyline points={points.map((point, index) => `${x(index)},${y(point[key])}`).join(" ")} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
            {points.map((point, index) => <circle key={point.from_at} cx={x(index)} cy={y(point[key])} r="2" fill={color}><title>{`${time(point.from_at)} · ${label} ${point[key]}`}</title></circle>)}
          </g>)}
          {points.map((point, index) => index % 3 === 0 || index === points.length - 1
            ? <text key={point.from_at} x={x(index)} y="138" textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"} fill="var(--theme-text-tertiary)" fontSize="10">{time(point.from_at)}</text> : null)}
        </svg>
        <ol className="sr-only">{points.map((point) => <li key={point.from_at}>{time(point.from_at)}：成功 {point.succeeded}，失败 {point.failed}，执行中 {point.running}</li>)}</ol>
      </> : <p className="flex min-h-32 flex-1 items-center justify-center text-xs text-[var(--theme-text-tertiary)]">趋势数据未采集</p>}
      <p className="mt-1 text-[11px] text-[var(--theme-text-tertiary)]">按提交时间分组 · 当前状态 · 北京时间</p>
    </section>
  );
}
