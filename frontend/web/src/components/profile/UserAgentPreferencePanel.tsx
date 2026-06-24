import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Save, Check, AlertCircle } from "lucide-react";
import { toast } from "react-hot-toast";
import { AgentInfo } from "../../types";
import { authApi } from "../../services/api/auth";
import { agentApi } from "../../services/api/agent";
import { useAuth } from "../../hooks/useAuth";
import { LoadingSpinner } from "../common/LoadingSpinner";

export function UserAgentPreferencePanel() {
  const { t } = useTranslation();
  const { user, refreshUser } = useAuth();
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [availableAgents, setAvailableAgents] = useState<AgentInfo[]>([]);
  const [currentPreference, setCurrentPreference] = useState<string | null>(
    null,
  );
  const [selectedAgent, setSelectedAgent] = useState<string>("");

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const agentsRes = await agentApi.list();
      const metadataDefaultAgentId =
        typeof user?.metadata?.defaultAgentId === "string"
          ? user.metadata.defaultAgentId
          : localStorage.getItem("defaultAgentId");

      setAvailableAgents(agentsRes.agents || []);
      setCurrentPreference(metadataDefaultAgentId || null);
      setSelectedAgent(
        metadataDefaultAgentId || agentsRes.default_agent || "",
      );
    } catch (err) {
      const errorMsg = (err as Error).message || t("agentConfig.loadFailed");
      setError(errorMsg);
    } finally {
      setIsLoading(false);
    }
  }, [t, user?.metadata?.defaultAgentId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSave = async () => {
    if (!selectedAgent) return;

    setIsSaving(true);
    try {
      await authApi.updateMetadata({ defaultAgentId: selectedAgent });
      localStorage.setItem("defaultAgentId", selectedAgent);
      setCurrentPreference(selectedAgent);
      void refreshUser();
      toast.success(t("agentConfig.preferenceSaved"));
      window.dispatchEvent(
        new CustomEvent("agent-preference-updated", {
          detail: { agentId: selectedAgent },
        }),
      );
    } catch (err) {
      toast.error((err as Error).message || t("agentConfig.saveFailed"));
    } finally {
      setIsSaving(false);
    }
  };

  const hasChanges = selectedAgent !== currentPreference;

  if (isLoading) {
    return (
      <div className="flex h-32 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <div className="es-error">
          <AlertCircle size={16} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="enterprise-subtle-panel">
        {availableAgents.length === 0 ? (
          <p className="py-2 text-sm text-[var(--theme-text-secondary)]">
            {t("agentConfig.noAvailableAgents")}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {availableAgents.map((agent) => (
              <label
                key={agent.id}
                className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-3 transition-colors ${
                  selectedAgent === agent.id
                    ? "border-teal-600/50 bg-teal-50/80 dark:border-teal-400/40 dark:bg-teal-500/10"
                    : "border-[var(--theme-border)] bg-[var(--theme-bg-card)] hover:bg-[var(--theme-bg)] dark:bg-stone-900 dark:hover:bg-stone-800"
                }`}
              >
                <input
                  type="radio"
                  name="defaultAgent"
                  value={agent.id}
                  checked={selectedAgent === agent.id}
                  onChange={(e) => setSelectedAgent(e.target.value)}
                  className="shrink-0 accent-teal-700"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-[var(--theme-text)]">
                    {t(agent.name)}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-[var(--theme-text-secondary)]">
                    {t(agent.description)}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      {hasChanges && (
        <div className="flex justify-end">
          <button
            onClick={handleSave}
            disabled={isSaving || !selectedAgent}
            className="btn-primary"
          >
            <span className="inline-flex h-4 w-4 items-center justify-center">
              {isSaving ? <LoadingSpinner size="sm" /> : <Save size={15} />}
            </span>
            <span>{t("common.save")}</span>
          </button>
        </div>
      )}

      {currentPreference && !hasChanges && (
        <div className="flex items-center gap-2 text-sm text-[var(--theme-text-secondary)]">
          <Check
            size={16}
            className="text-green-500 dark:text-green-400 shrink-0"
          />
          <span className="truncate">
            {t("agentConfig.currentPreference", {
              agentName: t(
                availableAgents.find((a) => a.id === currentPreference)?.name ||
                  currentPreference,
              ),
            })}
          </span>
        </div>
      )}
    </div>
  );
}
