import { Button } from "@lobehub/ui/base-ui";
import type { UsageMeta } from "../../services/api/modelUsage";
import { ApiRequestError } from "../../services/api/fetch";

export function UsageError({ error, retry }: { error: unknown; retry: () => void }) {
  const status = error instanceof ApiRequestError ? error.status : undefined;
  return <div role="alert" className="flex flex-wrap items-center gap-3 py-3 text-sm">
    <span>{status === 401 ? "登录已失效，请重新登录后查看用量。" : status === 403 ? "无权查看用量统计，仅管理员可访问。" : "用量加载失败，请重试。"}</span>
    {status !== 401 && status !== 403 && <Button onClick={retry}>重试</Button>}
  </div>;
}

export function UsageEmpty({ meta }: { meta: UsageMeta }) {
  return <span>{meta.coverage.status === "reported_final" ? "所选范围没有已记录用量" : "暂无可展示的用量记录；未采集不代表零用量"}</span>;
}
