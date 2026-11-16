import { useCallback, useEffect, useRef, useState } from "react";
import { adminRunsApi, type AdminRunDetailResponse, type AdminRunDiagnosticsResponse, type AdminRunInvestigation, type AdminRunInspection } from "../../services/api/adminRuns";
import { publicApiErrorMessage } from "../../services/api/fetch";

type Section<T> = { data: T | null; loading: boolean; error: string | null; updatedAt: string | null };
const empty = <T,>(): Section<T> => ({ data: null, loading: true, error: null, updatedAt: null });

export function useRunSection<T>(runId: string | null, read: (id: string) => Promise<T>) {
  const [section, setSection] = useState<Section<T>>(empty);
  const request = useRef(0);
  const activeRun = useRef(runId);
  activeRun.current = runId;
  const load = useCallback(async () => {
    if (!runId) return;
    const id = ++request.current;
    const current = () => id === request.current && runId === activeRun.current;
    setSection((previous) => ({ ...previous, loading: true, error: null }));
    try {
      const data = await read(runId);
      if (current()) setSection({ data, loading: false, error: null, updatedAt: new Date().toISOString() });
    } catch (error) {
      if (current()) setSection((previous) => ({ ...previous, loading: false, error: publicApiErrorMessage(error, "读取失败，请重试；已读取的记录仍可查看。") }));
    }
  }, [read, runId]);
  useEffect(() => {
    setSection(empty());
    void load();
    return () => { request.current += 1; };
  }, [load]);
  return { ...section, reload: load };
}
const readDetail = (id: string) => adminRunsApi.detail(id);
const readDiagnostics = (id: string) => adminRunsApi.diagnostics(id);
const readInvestigation = (id: string) => adminRunsApi.investigation(id);
const readInspection = (id: string) => adminRunsApi.inspection(id);

export function useRunDetail(runId: string | null) {
  const detail = useRunSection<AdminRunDetailResponse>(runId, readDetail);
  const diagnostics = useRunSection<AdminRunDiagnosticsResponse>(runId, readDiagnostics);
  const investigation = useRunSection<AdminRunInvestigation>(runId, readInvestigation);
  const inspection = useRunSection<AdminRunInspection>(runId, readInspection);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const generation = useRef(0);
  const activeRun = useRef(runId);
  const exportRequest = useRef(0);
  activeRun.current = runId;
  const { reload: reloadDetail } = detail;
  const { reload: reloadDiagnostics } = diagnostics;
  const { reload: reloadInvestigation } = investigation;
  const { reload: reloadInspection } = inspection;
  const refresh = useCallback(() => { void reloadDetail(); void reloadDiagnostics(); void reloadInvestigation(); void reloadInspection(); }, [reloadDetail, reloadDiagnostics, reloadInvestigation, reloadInspection]);
  useEffect(() => {
    generation.current += 1;
    setExporting(false); setExportError(null);
    return () => { generation.current += 1; };
  }, [runId]);

  const terminal = detail.data && ["succeeded", "failed", "cancelled"].includes(detail.data.run.status);
  useEffect(() => {
    if (!runId || terminal) return;
    const tick = () => { if (document.visibilityState === "visible") refresh(); };
    const timer = window.setInterval(tick, 5000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [refresh, runId, terminal]);

  const exportDiagnostics = useCallback(async () => {
    if (!runId || exporting) return;
    const owner = generation.current;
    const request = ++exportRequest.current;
    const current = () => generation.current === owner && activeRun.current === runId && exportRequest.current === request;
    setExporting(true); setExportError(null);
    try {
      const result = await adminRunsApi.exportDiagnostics(runId);
      if (!current()) return;
      const objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl; anchor.download = result.filename; anchor.rel = "noopener";
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    } catch (error) {
      if (current()) setExportError(publicApiErrorMessage(error, "诊断包生成失败，请重试。"));
    } finally { if (current()) setExporting(false); }
  }, [exporting, runId]);
  return { detail, diagnostics, investigation, inspection, refresh, exportDiagnostics, exporting, exportError };
}
