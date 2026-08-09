import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";

import { RoleSelector } from "../mcp/RoleSelector";
import { DepartmentDirectorySelector } from "./DepartmentDirectorySelector";
import { resolveDepartmentSelection } from "./departmentDirectorySelection";
import {
  capabilityDistributionApi,
  type CapabilityDistribution,
  type CapabilityDistributionStatus,
  type CapabilityDistributionUpdate,
  type DepartmentDirectoryNode,
} from "../../services/api/capabilityDistribution";
import { ApiRequestError } from "../../services/api/fetch";
import type { AdminSkillCatalogItem } from "../../services/api/skill";
import {
  buildControlledSkillDistributionUpdate,
  type DepartmentScopeMode,
} from "./skillDistributionDraft";

function createDraft(
  distribution?: CapabilityDistribution,
): CapabilityDistributionUpdate {
  return {
    status: distribution?.status ?? "active",
    visibleToUser: distribution?.visibleToUser ?? true,
    scopeMode: "allowlist",
    departmentIds: distribution?.departmentIds ?? [],
    allowedRoles: distribution?.allowedRoles ?? [],
    metadata: distribution?.metadata ?? {},
  };
}

function safeDirectoryError(): string {
  return "权威部门目录暂时不可用；在目录恢复前不能提交任何范围变更。";
}

function safeDistributionError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 401 || error.status === 403) {
      return "当前账号无权管理 Skill 可见范围。";
    }
    if (error.status === 404) {
      return "该 Skill 或其可见范围记录已不存在，请刷新后重试。";
    }
    if (error.status === 409) {
      return "该可见范围当前不可修改，请刷新后确认状态。";
    }
  }
  return "暂时无法保存 Skill 可见范围，请稍后重试。";
}

interface SkillDistributionGovernancePanelProps {
  selectedSkill: AdminSkillCatalogItem | null;
  selectedSkillId: string | null;
}

/** Controlled editor for the Skill selected by the page's one canonical catalog. */
export function SkillDistributionGovernancePanel({
  selectedSkill,
  selectedSkillId,
}: SkillDistributionGovernancePanelProps) {
  const [distributions, setDistributions] = useState<CapabilityDistribution[]>([]);
  const [draft, setDraft] = useState<CapabilityDistributionUpdate | null>(null);
  const [departmentScope, setDepartmentScope] =
    useState<DepartmentScopeMode>("all");
  const [directory, setDirectory] = useState<DepartmentDirectoryNode[] | null>(null);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const distributionBySkillId = useMemo(
    () => new Map(distributions.map((item) => [item.capabilityId, item])),
    [distributions],
  );
  const selectedDistribution = selectedSkillId
    ? distributionBySkillId.get(selectedSkillId)
    : undefined;
  const departmentSelection = useMemo(
    () => resolveDepartmentSelection(draft?.departmentIds ?? [], directory),
    [directory, draft?.departmentIds],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setDirectoryError(null);
    try {
      const [distributionResult, directoryResult] = await Promise.allSettled([
        capabilityDistributionApi.list("skill"),
        capabilityDistributionApi.departmentDirectory(),
      ]);
      if (distributionResult.status === "rejected") {
        throw distributionResult.reason;
      }
      setDistributions(distributionResult.value);
      if (directoryResult.status === "fulfilled") {
        setDirectory(directoryResult.value);
      } else {
        setDirectory(null);
        setDirectoryError(safeDirectoryError());
      }
    } catch (error) {
      setLoadError(safeDistributionError(error));
      setDistributions([]);
      setDraft(null);
      setDirectory(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setDraft(selectedSkillId ? createDraft(selectedDistribution) : null);
    setDepartmentScope(
      selectedDistribution?.departmentIds.length ? "restricted" : "all",
    );
    setSaveError(null);
    setSaved(false);
  }, [selectedDistribution, selectedSkillId]);

  const save = async () => {
    if (!selectedSkillId || !draft || saving) return;
    if (
      directory === null ||
      !departmentSelection.authoritative ||
      (departmentScope === "restricted" && draft.departmentIds.length === 0)
    ) {
      setSaveError("当前部门范围尚未通过权威目录确认，不能保存。");
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    const update = buildControlledSkillDistributionUpdate(
      draft,
      departmentScope,
    );
    try {
      const savedDistribution = await capabilityDistributionApi.update(
        "skill",
        selectedSkillId,
        update,
      );
      setDistributions((current) => [
        ...current.filter(
          (item) => item.capabilityId !== savedDistribution.capabilityId,
        ),
        savedDistribution,
      ]);
      setDraft(createDraft(savedDistribution));
      setSaved(true);
    } catch (error) {
      setSaveError(safeDistributionError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      aria-label="Skill 可见范围"
      className="border-t border-[var(--theme-border)] px-4 py-4"
      data-skill-distribution-governance
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--theme-text)]">
            Skill 可见范围
          </h2>
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            配置该 Skill 对哪些用户、部门和角色可见；实际使用权限仍由服务端校验。
          </p>
        </div>
        <button
          aria-label="刷新 Skill 可见范围"
          className="btn-icon"
          disabled={loading || saving}
          onClick={() => void load()}
          title="刷新"
          type="button"
        >
          <RefreshCw
            aria-hidden="true"
            className={loading ? "animate-spin" : undefined}
            size={16}
          />
        </button>
      </div>

      {loadError ? (
        <div
          className="mt-3 border-l-2 border-[var(--theme-danger)] px-3 py-2 text-sm text-[var(--theme-danger)]"
          role="alert"
        >
          {loadError}
        </div>
      ) : null}

      {loading ? (
        <div
          className="mt-4 flex items-center gap-2 text-sm text-[var(--theme-text-secondary)]"
          data-skill-distribution-loading
          role="status"
        >
          <RefreshCw aria-hidden="true" className="animate-spin" size={15} />
          正在加载 Skill 与权威部门目录
        </div>
      ) : null}

      {!loadError && !loading ? (
        <div className="mt-4 min-h-0">
          {selectedSkillId && draft ? (
            <div className="min-w-0">
              {selectedSkill ? (
                <div
                  className="skill-authority-rail mb-4"
                  data-skill-authority-rail
                >
                  <div>
                    <span>Admin release</span>
                    <strong>
                      {selectedSkill.currentVersion
                        ? `stable ${selectedSkill.currentVersion}`
                        : "尚无 stable 版本"}
                    </strong>
                    <small>
                      {selectedSkill.latestVersion
                        ? `最新 ${selectedSkill.latestVersion} · ${selectedSkill.latestVersionStatus}`
                        : "尚无版本包"}
                    </small>
                  </div>
                  <div>
                    <span>用户可见范围</span>
                    <strong>
                      {(selectedDistribution?.status ??
                        selectedSkill.distributionStatus) === "active" &&
                      (selectedDistribution?.visibleToUser ??
                        selectedSkill.visibleToUser)
                        ? "启用且可见"
                        : (selectedDistribution?.status ??
                              selectedSkill.distributionStatus) === "active"
                          ? "启用但隐藏"
                          : "已停用"}
                    </strong>
                    <small>
                      {draft.departmentIds.length > 0
                        ? `${draft.departmentIds.length} 个部门范围`
                        : "不限制部门"}
                    </small>
                  </div>
                </div>
              ) : null}
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="flex items-center gap-2.5 text-sm text-[var(--theme-text)]">
                  <input
                    checked={draft.visibleToUser}
                    data-skill-distribution-visible
                    disabled={saving}
                    onChange={(event) =>
                      setDraft((current) =>
                        current
                          ? { ...current, visibleToUser: event.target.checked }
                          : current,
                      )
                    }
                    type="checkbox"
                  />
                  对普通用户可见
                </label>
                <label className="es-field">
                  <span className="es-label">状态</span>
                  <select
                    className="enterprise-field-control es-input"
                    data-skill-distribution-status
                    disabled={saving}
                    onChange={(event) =>
                      setDraft((current) =>
                        current
                          ? {
                              ...current,
                              status: event.target.value as CapabilityDistributionStatus,
                            }
                          : current,
                      )
                    }
                    value={draft.status}
                  >
                    <option value="active">启用</option>
                    <option value="disabled">停用</option>
                  </select>
                </label>
              </div>

              <fieldset className="es-field mt-4">
                <legend className="es-label">部门范围</legend>
                <div className="mt-2 flex flex-wrap gap-2" data-skill-distribution-department-mode>
                  <label className="inline-flex items-center gap-2 text-sm text-[var(--theme-text)]">
                    <input
                      checked={departmentScope === "all"}
                      disabled={saving}
                      name="skill-department-scope"
                      onChange={() => setDepartmentScope("all")}
                      type="radio"
                    />
                    全部部门
                  </label>
                  <label className="inline-flex items-center gap-2 text-sm text-[var(--theme-text)]">
                    <input
                      checked={departmentScope === "restricted"}
                      disabled={saving}
                      name="skill-department-scope"
                      onChange={() => setDepartmentScope("restricted")}
                      type="radio"
                    />
                    指定部门
                  </label>
                </div>
              </fieldset>

              {departmentScope === "restricted" ? (
              <div className="es-field mt-4">
                <span className="es-label">允许部门</span>
                <span className="es-hint">
                  仅可选择服务端权威目录中的部门；留空表示不限制部门。
                </span>
                <DepartmentDirectorySelector
                  directory={directory}
                  disabled={saving}
                  loadError={directoryError}
                  onChange={(departmentIds) =>
                    setDraft((current) =>
                      current
                        ? { ...current, departmentIds }
                        : current,
                    )
                  }
                  selectedAuthorityIds={draft.departmentIds}
                />
              </div>
              ) : null}

              <div className="es-field mt-4">
                <span className="es-label">允许角色</span>
                <span className="es-hint">沿用 MCP 权限编辑器的角色选择方式。</span>
                <div className="mt-1">
                  <RoleSelector
                    onChange={(allowedRoles) =>
                      setDraft((current) =>
                        current ? { ...current, allowedRoles } : current,
                      )
                    }
                    selectedRoles={draft.allowedRoles}
                  />
                </div>
              </div>

              <div className="mt-5 flex flex-wrap items-center gap-3">
                <button
                  className="btn-primary inline-flex items-center gap-2 disabled:opacity-60"
                  disabled={
                    saving ||
                    directory === null ||
                    !departmentSelection.authoritative ||
                    (departmentScope === "restricted" &&
                      draft.departmentIds.length === 0)
                  }
                  onClick={() => void save()}
                  type="button"
                >
                  {saving ? (
                    <RefreshCw className="animate-spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  保存可见范围
                </button>
                {saved ? (
                  <span className="inline-flex items-center gap-1 text-sm text-[var(--theme-success)]" role="status">
                    <ShieldCheck size={16} /> 已保存
                  </span>
                ) : null}
                {saveError ? (
                  <span className="text-sm text-[var(--theme-danger)]" role="alert">
                    {saveError}
                  </span>
                ) : null}
              </div>
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-[var(--theme-text-secondary)]">
              请从 Skill 目录选择一项查看详情。
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}
