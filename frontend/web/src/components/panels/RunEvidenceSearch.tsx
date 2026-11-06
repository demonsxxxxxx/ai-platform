import { Button, Input } from "@lobehub/ui/base-ui";
import { useEffect, useRef, useState } from "react";
import { adminRunsApi, type AdminRunEvidenceSearch } from "../../services/api/adminRuns";
import { publicApiErrorMessage } from "../../services/api/fetch";

export function RunEvidenceSearch({ runId, attemptId, onSelect }: { runId: string; attemptId: string; onSelect: (sequence?: number, observation?: string, afterSequence?: number) => void }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<AdminRunEvidenceSearch | null>(null);
  const [segment, setSegment] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const owner = useRef(0);
  useEffect(() => { const token = owner; token.current++; setResult(null); setError(null); setBusy(false); return () => { token.current++; }; }, [runId, attemptId]);
  const search = async (after = 0, offset = 0) => {
    if (!query.trim()) return;
    const generation = ++owner.current; setBusy(true); setError(null);
    try {
      const next = await adminRunsApi.searchEvidence(runId, query.trim(), after, attemptId === "all" ? undefined : attemptId, offset);
      if (generation === owner.current) { setResult(next); setSegment(after); }
    } catch (failure) { if (generation === owner.current) setError(publicApiErrorMessage(failure, "证据检索失败，请重试。")); }
    finally { if (generation === owner.current) setBusy(false); }
  };
  return <section className="run-detail-section"><h2>服务端证据检索</h2><p className="run-detail-note">每次扫描最多 1000 条已保存事件及首批保留诊断，只查可披露字段。搜索不到不代表事件未发生；不检索消息分块正文、提示词和私有载荷。</p><form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); void search(); }}><Input aria-label="检索此运行的安全证据" maxLength={200} value={query} onChange={(event) => { owner.current++; setBusy(false); setResult(null); setQuery(event.target.value); }} placeholder="输入事件、错误或调用编号" /><Button htmlType="submit" loading={busy} disabled={!query.trim()}>检索</Button></form>{error ? <p role="alert">{error}</p> : null}{result ? <><p className="mt-2 text-xs">本段扫描 {result.searched_source_count} 条源事件 · {result.matches.length} 个命中{result.matches_truncated ? " · 命中超限，请缩小检索条件" : ""}</p>{result.payload_omitted_count ? <p className="run-detail-note">本段 {result.payload_omitted_count} 条记录超出安全读取预算，未检索其内容；游标仍可继续。</p> : null}<ul>{result.matches.map((item, index) => <li key={index}><Button type="text" onClick={() => onSelect(item.sequence, item.observation_id, item.context_after_sequence)}>{item.sequence ? `#${item.sequence}` : "诊断观察"} · {item.summary}</Button></li>)}</ul>{!result.matches.length ? <p>当前已扫描范围没有匹配证据。</p> : null}{result.matches_truncated ? <Button loading={busy} onClick={() => void search(segment, result.next_match_offset)}>查看本段后续命中</Button> : null}{result.has_more ? <Button loading={busy} onClick={() => void search(result.next_after_sequence)}>检索后续证据</Button> : <p className="text-xs">本次已到保存事件末尾。</p>}</> : null}</section>;
}
