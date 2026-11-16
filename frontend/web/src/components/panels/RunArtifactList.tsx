import { Button } from "@lobehub/ui/base-ui";
import { useEffect, useRef, useState } from "react";
import type { AdminRunArtifact } from "../../services/api/adminRuns";
import { authFetch, ApiRequestError, apiRequestErrorFromResponse, publicApiErrorMessage } from "../../services/api/fetch";
import { authenticatedRequest } from "../../services/api/authenticatedRequest";
import { downloadAuthenticatedFile } from "../../services/api/download";
import { buildArtifactPreviewRequest } from "../chat/ChatMessage/items/artifactPreview";
import type { RevealPreviewRequest } from "../chat/ChatMessage/items/revealPreviewData";
import DocumentPreview from "../documents/DocumentPreview";
import { byteSizeLabel } from "./runPresentation";

type Access = { access: "allowed" | "denied" | "unknown"; availability: "unverified" | "available" | "missing" | "expired"; preview_allowed: boolean };
function RunArtifact({ runId, artifact }: { runId: string; artifact: AdminRunArtifact }) {
  const [access, setAccess] = useState<Access | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<RevealPreviewRequest | null>(null);
  const generation = useRef(0);
  useEffect(() => { const token = generation; token.current++; return () => { token.current++; }; }, [runId, artifact.artifact_id]);
  const base = `/api/ai/admin/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifact.artifact_id)}`;
  const act = async (action: "access" | "preview" | "download") => {
    const owner = generation.current; setBusy(true); setError(null);
    try {
      if (action === "access") {
        const result = await authFetch<Access>(`${base}/access`);
        if (owner === generation.current) setAccess(result);
      } else if (action === "download") {
        await downloadAuthenticatedFile(`${base}/download`, artifact.label, { isCurrent: () => owner === generation.current, request: async (input, init) => {
          const response = await authenticatedRequest(input, init);
          if (!response.ok) throw await apiRequestErrorFromResponse(response);
          if (owner !== generation.current) throw new Error("artifact_request_no_longer_current");
          return response;
        } });
        if (owner === generation.current) setAccess((current) => current ? { ...current, availability: "available" } : current);
      } else {
        const request = buildArtifactPreviewRequest({ ...artifact, preview_url: `${base}/preview`, download_url: `${base}/download` });
        if (request) setPreview(request);
      }
    } catch (failure) {
      if (owner !== generation.current) return;
      if (failure instanceof ApiRequestError && [403, 404, 410].includes(failure.status)) {
        setAccess({ access: failure.status === 403 ? "denied" : "unknown", availability: failure.status === 410 ? "expired" : failure.status === 404 ? "missing" : "unverified", preview_allowed: false });
      }
      setError(publicApiErrorMessage(failure, "文件访问失败，请重新检查权限或稍后重试。"));
    } finally { if (owner === generation.current) setBusy(false); }
  };
  const available = access?.access === "allowed" && ["unverified", "available"].includes(access.availability);
  return <li className="py-3"><p className="break-words font-medium">{artifact.label}</p><p className="mt-1 text-xs">{artifact.content_type} · {byteSizeLabel(artifact.size_bytes)} · {{ unverified: "文件可用性未核验", available: "最近读取成功", missing: "文件缺失或不可访问", expired: "已过期" }[access?.availability || "unverified"]}{access?.access === "denied" ? " · 无访问权限" : ""}</p>
    <div className="mt-2 flex flex-wrap gap-2"><Button size="small" type="text" loading={busy} onClick={() => void act("access")}>检查访问权限</Button>{available ? <><Button size="small" onClick={() => void act("download")} loading={busy}>下载</Button>{access.preview_allowed ? <Button size="small" onClick={() => void act("preview")}>预览</Button> : null}</> : null}</div>{error ? <p role="alert" className="mt-2 text-sm">{error}</p> : null}{preview ? <DocumentPreview path={preview.filePath} previewUrl={preview.previewUrl} downloadUrl={preview.downloadUrl} mimeType={preview.mimeType} fileSize={preview.fileSize} onClose={() => setPreview(null)} /> : null}</li>;
}
export function RunArtifactList({ runId, artifacts }: { runId: string; artifacts: AdminRunArtifact[] }) {
  return <section className="run-detail-section"><h2>已登记产物（{artifacts.length}）</h2><p className="run-detail-note">登记不等于文件可访问。权限检查不读取文件；预览和下载时重新授权。</p>{artifacts.length ? <ul className="divide-y divide-[var(--theme-border)]">{artifacts.map((artifact) => <RunArtifact key={`${runId}:${artifact.artifact_id}`} runId={runId} artifact={artifact} />)}</ul> : <p>没有已登记产物。</p>}</section>;
}
