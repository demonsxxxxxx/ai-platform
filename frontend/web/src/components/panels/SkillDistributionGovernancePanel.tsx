import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useAuth } from "../../hooks/useAuth";

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

function safeDistributionErrorKey(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 401 || error.status === 403) {
      return "skills.governance.errors.forbidden";
    }
    if (error.status === 404) {
      return "skills.governance.errors.notFound";
    }
    if (error.status === 409) {
      return "skills.governance.errors.conflict";
    }
  }
  return "skills.governance.errors.saveFailed";
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
  const { t } = useTranslation();
  const { user, isAuthenticated } = useAuth();
  const authScope = JSON.stringify([user?.tenant_id, user?.id, isAuthenticated]);
  const mounted = useRef(false);
  const authGeneration = useRef(0);
  const authScopeRef = useRef(authScope);
  const selectionGeneration = useRef(0);
  const loadSequence = useRef(0);
  const activeSave = useRef<object | null>(null);
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

  useLayoutEffect(() => {
    mounted.current = true;
    authScopeRef.current = authScope;
    setLoading(true);
    setDraft(null);
    setLoadError(null);
    authGeneration.current += 1;
    loadSequence.current += 1;
    setDistributions([]);
    setDirectory(null);
    return () => {
      mounted.current = false;
      authGeneration.current += 1;
      loadSequence.current += 1;
      activeSave.current = null;
    };
  }, [authScope]);

  useLayoutEffect(() => {
    selectionGeneration.current += 1;
    activeSave.current = null;
    setSaving(false);
    setSaveError(null);
    setSaved(false);
  }, [authScope, selectedSkillId]);

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
    const request = ++loadSequence.current;
    const generation = authGeneration.current;
    const ownsRequest = () => mounted.current && authScopeRef.current === authScope && generation === authGeneration.current && request === loadSequence.current;
    if (!isAuthenticated) { setLoading(false); return; }
    setLoading(true);
    setLoadError(null);
    setDirectoryError(null);
    setSaved(false);
    try {
      const [distributionResult, directoryResult] = await Promise.allSettled([
        capabilityDistributionApi.list("skill"),
        capabilityDistributionApi.departmentDirectory(),
      ]);
      if (!ownsRequest()) return;
      if (distributionResult.status === "rejected") {
        throw distributionResult.reason;
      }
      setDistributions(distributionResult.value);
      if (directoryResult.status === "fulfilled") {
        setDirectory(directoryResult.value);
      } else {
        setDirectory(null);
        setDirectoryError(t("skills.governance.errors.directoryUnavailable"));
      }
    } catch (error) {
      if (!ownsRequest()) return;
      setLoadError(t(safeDistributionErrorKey(error)));
      setDistributions([]);
      setDraft(null);
      setDirectory(null);
    } finally {
      if (ownsRequest()) setLoading(false);
    }
  }, [t, authScope, isAuthenticated]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setDraft(selectedSkillId ? createDraft(selectedDistribution) : null);
    setDepartmentScope(
      selectedDistribution?.departmentIds.length ? "restricted" : "all",
    );
  }, [selectedDistribution, selectedSkillId]);

  const save = async () => {
    if (!mounted.current || !selectedSkillId || !draft || loading || activeSave.current) return;
    if (
      directory === null ||
      !departmentSelection.authoritative ||
      (departmentScope === "restricted" && draft.departmentIds.length === 0)
    ) {
      setSaveError(t("skills.governance.errors.directoryNotAuthoritative"));
      return;
    }
    const request = {};
    activeSave.current = request;
    const generation = authGeneration.current;
    const selection = selectionGeneration.current;
    const ownsScope = () => mounted.current && generation === authGeneration.current;
    const ownsEditor = () => ownsScope() && selection === selectionGeneration.current && activeSave.current === request;
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
      if (!ownsEditor()) return;
      if (savedDistribution.capabilityId !== selectedSkillId) throw new Error("distribution_receipt_mismatch");
      setDistributions((current) => [
        ...current.filter(
          (item) => item.capabilityId !== savedDistribution.capabilityId,
        ),
        savedDistribution,
      ]);
      if (!ownsEditor()) return;
      setDraft(createDraft(savedDistribution));
      setSaved(true);
    } catch (error) {
      if (ownsEditor()) setSaveError(t(safeDistributionErrorKey(error)));
    } finally {
      if (ownsEditor()) {
        activeSave.current = null;
        setSaving(false);
      }
    }
  };

  return (
    <section
      aria-label={t("skills.governance.title")}
      className="px-4 py-4 sm:p-5"
      data-skill-distribution-governance
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--theme-text)]">
            {t("skills.governance.title")}
          </h2>
          <p className="mt-1 text-xs leading-5 text-[var(--theme-text-secondary)]">
            {t("skills.governance.description")}
          </p>
        </div>
        <button
          aria-label={t("skills.governance.refresh")}
          className="btn-icon"
          disabled={loading || saving}
          onClick={() => void load()}
          title={t("skills.governance.refreshShort")}
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
          {t("skills.governance.loading")}
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
                    <span>{t("skills.governance.release.title")}</span>
                    <strong>
                      {selectedSkill.currentDisplayVersion
                        ? t("skills.governance.release.current", {
                            version: selectedSkill.currentDisplayVersion,
                          })
                        : t("skills.governance.release.none")}
                    </strong>
                    <small>
                      {selectedSkill.latestDisplayVersion
                        ? t("skills.governance.release.latest", {
                            version: selectedSkill.latestDisplayVersion,
                          })
                        : t("skills.governance.release.noPackage")}
                    </small>
                  </div>
                  <div>
                    <span>{t("skills.governance.visibility.title")}</span>
                    <strong>
                      {(selectedDistribution?.status ??
                        selectedSkill.distributionStatus) === "active" &&
                      (selectedDistribution?.visibleToUser ??
                        selectedSkill.visibleToUser)
                        ? t("skills.governance.visibility.activeVisible")
                        : (selectedDistribution?.status ??
                              selectedSkill.distributionStatus) === "active"
                          ? t("skills.governance.visibility.activeHidden")
                          : t("skills.governance.visibility.disabled")}
                    </strong>
                    <small>
                      {draft.departmentIds.length > 0
                        ? t("skills.governance.visibility.departmentCount", {
                            count: draft.departmentIds.length,
                          })
                        : t("skills.governance.visibility.allDepartments")}
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
                  {t("skills.governance.visibleToUsers")}
                </label>
                <label className="es-field">
                  <span className="es-label">{t("skills.governance.status.label")}</span>
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
                    <option value="active">{t("skills.governance.status.active")}</option>
                    <option value="disabled">{t("skills.governance.status.disabled")}</option>
                  </select>
                </label>
              </div>

              <fieldset className="es-field mt-4">
                <legend className="es-label">{t("skills.governance.departments.scope")}</legend>
                <div className="mt-2 flex flex-wrap gap-2" data-skill-distribution-department-mode>
                  <label className="inline-flex items-center gap-2 text-sm text-[var(--theme-text)]">
                    <input
                      checked={departmentScope === "all"}
                      disabled={saving}
                      name="skill-department-scope"
                      onChange={() => setDepartmentScope("all")}
                      type="radio"
                    />
                    {t("skills.governance.departments.all")}
                  </label>
                  <label className="inline-flex items-center gap-2 text-sm text-[var(--theme-text)]">
                    <input
                      checked={departmentScope === "restricted"}
                      disabled={saving}
                      name="skill-department-scope"
                      onChange={() => setDepartmentScope("restricted")}
                      type="radio"
                    />
                    {t("skills.governance.departments.restricted")}
                  </label>
                </div>
              </fieldset>

              {departmentScope === "restricted" ? (
              <div className="es-field mt-4">
                <span className="es-label">{t("skills.governance.departments.allowed")}</span>
                <span className="es-hint">
                  {t("skills.governance.departments.hint")}
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
                <span className="es-label">{t("skills.governance.roles.allowed")}</span>
                <span className="es-hint">{t("skills.governance.roles.hint")}</span>
                <fieldset className="mt-1" disabled={saving}>
                  <RoleSelector
                    onChange={(allowedRoles) =>
                      setDraft((current) =>
                        current ? { ...current, allowedRoles } : current,
                      )
                    }
                    selectedRoles={draft.allowedRoles}
                  />
                </fieldset>
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
                  data-skill-distribution-save
                  onClick={() => void save()}
                  type="button"
                >
                  {saving ? (
                    <RefreshCw className="animate-spin" size={16} />
                  ) : (
                    <Save size={16} />
                  )}
                  {t("skills.governance.save")}
                </button>
                {saved ? (
                  <span className="inline-flex items-center gap-1 text-sm text-[var(--theme-success)]" role="status">
                    <ShieldCheck size={16} /> {t("skills.governance.saved")}
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
              {t("skills.governance.selectPrompt")}
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}
