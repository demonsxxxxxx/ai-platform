import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";

import { RoleSelector } from "../mcp/RoleSelector";
import {
  capabilityDistributionApi,
  type CapabilityDistribution,
  type CapabilityDistributionStatus,
  type CapabilityDistributionUpdate,
} from "../../services/api/capabilityDistribution";
import { ApiRequestError } from "../../services/api/fetch";
import { skillApi, type AdminSkillCatalogItem } from "../../services/api/skill";

interface DistributionDraft extends CapabilityDistributionUpdate {
  departmentInput: string;
}

function parseDepartmentIds(value: string): string[] {
  return [
    ...new Set(
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function createDraft(distribution?: CapabilityDistribution): DistributionDraft {
  return {
    status: distribution?.status ?? "active",
    visibleToUser: distribution?.visibleToUser ?? true,
    scopeMode: "allowlist",
    departmentIds: distribution?.departmentIds ?? [],
    departmentInput: distribution?.departmentIds.join(", ") ?? "",
    allowedRoles: distribution?.allowedRoles ?? [],
    metadata: distribution?.metadata ?? {},
  };
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
  const [draft, setDraft] = useState<DistributionDraft | null>(null);
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

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [nextSkills, nextDistributions] = await Promise.all([
        skillApi.adminListSkills(),
        capabilityDistributionApi.list("skill"),
      ]);
      setSkills(nextSkills);
      setDistributions(nextDistributions);
      setSelectedSkillId((current) =>
        current && nextSkills.some((skill) => skill.skillId === current)
          ? current
          : nextSkills[0]?.skillId ?? null,
      );
    } catch (error) {
      setLoadError(safeDistributionError(error));
      setSkills([]);
      setDistributions([]);
      setSelectedSkillId(null);
      setDraft(null);
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
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    const update: CapabilityDistributionUpdate = {
      ...draft,
      departmentIds: parseDepartmentIds(draft.departmentInput),
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

              <label className="es-field mt-4">
                <span className="es-label">允许部门</span>
                <span className="es-hint">用逗号分隔部门标识；留空时由服务端按当前分发规则处理。</span>
                <input
                  className="enterprise-field-control es-input mt-1"
                  data-skill-distribution-departments
                  disabled={saving}
                  onChange={(event) =>
                    setDraft((current) =>
                      current
                        ? { ...current, departmentInput: event.target.value }
                        : current,
                    )
                  }
                  placeholder="例如：研发部, 财务部"
                  type="text"
                  value={draft.departmentInput}
                />
              </label>

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
                  disabled={saving}
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
