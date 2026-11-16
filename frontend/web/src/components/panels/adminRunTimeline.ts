import type {
  AdminRunDiagnosticToolEvidence,
  AdminRunSummary,
} from "../../services/api/adminRuns";

function normalized(value: string | null | undefined): string { return value?.trim().toLowerCase() ?? ""; }
export function filterAdminRuns(runs: AdminRunSummary[], status: string, query: string): AdminRunSummary[] {
  const text = normalized(query);
  return runs.filter((run) => (status === "all" || run.status === status || (status === "running" && run.status === "cancel_requested")) && (!text || [run.session_title, run.task_summary, run.user_display_name, run.workspace_name, run.agent_name, run.skill_name, run.run_id, run.session_id, run.user_id, run.workspace_id, run.agent_id, run.skill_id, run.trace_id, run.error_code].some((value) => normalized(value).includes(text))));
}
export function indexToolDiagnosticEvidence(evidence: AdminRunDiagnosticToolEvidence[]) {
  const indexed = new Map<string, AdminRunDiagnosticToolEvidence>();
  const conflicts = new Set<string>();
  for (const item of evidence) {
    // Public operations and legacy native-ID actions remain separate aliases.
    for (const key of new Set([item.operation_id, item.invocation_id])) {
      if (!key || !key.trim() || conflicts.has(key)) continue;
      const previous = indexed.get(key);
      if (previous && (previous.tool_name !== item.tool_name
        || previous.invocation_id !== item.invocation_id
        || previous.operation_id !== item.operation_id)) {
        indexed.delete(key);
        conflicts.add(key);
      } else {
        indexed.set(key, item);
      }
    }
  }
  return indexed;
}
