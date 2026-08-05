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
import { skillApi, type AdminSkillCatalogItem } from "../../services/api/skill";

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
  return "权威部门目录暂时不可用；可清除现有部门范围，但不能提交非空范围。";
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

function statusLabel(status: CapabilityDistributionStatus): string {
  return status === "active" ? "启用" : "停用";
}

/** Server-backed admin editor for Skill distribution; it never grants client-side access. */
export function SkillDistributionGovernancePanel() {
  const [skills, setSkills] = useState<AdminSkillCatalogItem[]>([]);
  const [distributions, setDistributions] = useState<CapabilityDistribution[]>([]);
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [draft, setDraft] = useState<CapabilityDistributionUpdate | null>(null);
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
  const selectedSkill = selectedSkillId
    ? skills.find((skill) => skill.skillId === selectedSkillId)
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
      const [catalogResult, directoryResult] = await Promise.allSettled([
        Promise.all([
          skillApi.adminListSkills(),
          capabilityDistributionApi.list("skill"),
        ]),
        capabilityDistributionApi.departmentDirectory(),
      ]);
      if (catalogResult.status === "rejected") throw catalogResult.reason;
      const [nextSkills, nextDistributions] = catalogResult.value;
      setSkills(nextSkills);
      setDistributions(nextDistributions);
      setSelectedSkillId((current) =>
        current && nextSkills.some((skill) => skill.skillId === current)
          ? current
          : nextSkills[0]?.skillId ?? null,
      );
      if (directoryResult.status === "fulfilled") {
        setDirectory(directoryResult.value);
      } else {
        setDirectory(null);
        setDirectoryError(safeDirectoryError());
      }
    } catch (error) {
      setLoadError(safeDistributionError(error));
      setSkills([]);
      setDistributions([]);
      setSelectedSkillId(null);
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
    setSaveError(null);
    setSaved(false);
  }, [selectedDistribution, selectedSkillId]);

  const selectSkill = (skillId: string) => {
    setSelectedSkillId(skillId);
  };

  const save = async () => {
    if (!selectedSkillId || !draft || saving) return;
    if (!departmentSelection.authoritative) {
      setSaveError("当前部门范围尚未通过权威目录确认，不能保存。");
      return;
    }
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    const update: CapabilityDistributionUpdate = {
      ...draft,
      departmentIds: [...draft.departmentIds],
      metadata: { ...draft.metadata },
    };
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
            此处仅配置服务端 capability-distribution；普通用户目录和运行准入仍由服务端过滤。
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
        <div className="mt-4 grid min-h-0 gap-4 lg:grid-cols-[minmax(14rem,0.8fr)_minmax(0,1.2fr)]">
          <div className="border-r border-[var(--theme-border)] pr-4">
            <div className="space-y-1" role="list" aria-label="Skill 列表">
              {skills.map((skill) => {
                const distribution = distributionBySkillId.get(skill.skillId);
                const active = skill.skillId === selectedSkillId;
                return (
                  <button
                    aria-pressed={active}
                    className={`flex w-full items-center justify-between gap-3 border-l-2 px-2 py-2 text-left text-sm transition-colors ${
                      active
                        ? "border-[var(--theme-primary)] bg-[var(--theme-bg-sidebar)]"
                        : "border-transparent hover:bg-[var(--theme-bg-sidebar)]"
                    }`}
                    key={skill.skillId}
                    onClick={() => selectSkill(skill.skillId)}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-[var(--theme-text)]">
                        {skill.name}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-[var(--theme-text-secondary)]">
                        {skill.description || "未提供说明"}
                      </span>
                    </span>
                    <span className="shrink-0 text-xs text-[var(--theme-text-secondary)]">
                      {distribution ? statusLabel(distribution.status) : "未配置"}
                    </span>
                  </button>
                );
              })}
              {skills.length === 0 ? (
                <p className="px-2 py-4 text-sm text-[var(--theme-text-secondary)]">
                  当前没有可配置的 Skill。
                </p>
              ) : null}
            </div>
          </div>

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
                    <span>租户 capability-distribution</span>
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
                      {(selectedDistribution?.departmentIds.length ?? 0) > 0
                        ? `${selectedDistribution?.departmentIds.length} 个部门范围`
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
                  disabled={saving || !departmentSelection.authoritative}
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
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
