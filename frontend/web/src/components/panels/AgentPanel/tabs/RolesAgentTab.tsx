import { useState, useEffect } from "react";
import { Save } from "lucide-react";
import { useTranslation } from "react-i18next";
import { AgentPanelSkeleton } from "../../../skeletons";
import { RoleSelector } from "../shared/RoleSelector";
import type { Role, AgentInfo } from "../../../../types";

interface RolesAgentTabProps {
  roles: Role[];
  roleAgentsMap: Record<string, string[]>;
  availableAgents: AgentInfo[];
  onUpdate: (roleId: string, agentIds: string[]) => Promise<void>;
  isLoading: boolean;
}

export function RolesAgentTab({
  roles,
  roleAgentsMap,
  availableAgents,
  onUpdate,
  isLoading,
}: RolesAgentTabProps) {
  const { t } = useTranslation();
  const [selectedRole, setSelectedRole] = useState<string | null>(
    roles.length > 0 ? roles[0].id : null,
  );
  const [localRoleAgents, setLocalRoleAgents] =
    useState<Record<string, string[]>>(roleAgentsMap);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setLocalRoleAgents(roleAgentsMap);
  }, [roleAgentsMap]);

  // Reset selectedRole if it no longer exists in the roles list
  useEffect(() => {
    if (selectedRole && !roles.find((r) => r.id === selectedRole)) {
      setSelectedRole(roles.length > 0 ? roles[0].id : null);
    }
  }, [roles, selectedRole]);

  if (isLoading) {
    return <AgentPanelSkeleton />;
  }

  const currentRoleAgents = selectedRole
    ? localRoleAgents[selectedRole] || []
    : [];

  const toggleAgent = (agentId: string) => {
    if (!selectedRole) return;
    setLocalRoleAgents((prev) => {
      const current = prev[selectedRole] || [];
      if (current.includes(agentId)) {
        return {
          ...prev,
          [selectedRole]: current.filter((id) => id !== agentId),
        };
      }
      return { ...prev, [selectedRole]: [...current, agentId] };
    });
  };

  const handleSave = async () => {
    if (!selectedRole) return;
    setIsSaving(true);
    try {
      await onUpdate(selectedRole, localRoleAgents[selectedRole] || []);
    } catch (err) {
      console.error("Failed to save role agents:", err);
    } finally {
      setIsSaving(false);
    }
  };

  const selectedRoleData = roles.find((r) => r.id === selectedRole);
  const hasChanges = selectedRole
    ? JSON.stringify(localRoleAgents[selectedRole]) !==
      JSON.stringify(roleAgentsMap[selectedRole])
    : false;

  return (
    <div className="space-y-5">
      <p className="text-sm text-stone-500 dark:text-stone-400 px-1 leading-relaxed">
        {t("agentConfig.rolesDescription")}
      </p>

      <RoleSelector
        roles={roles}
        selectedRoleId={selectedRole}
        onSelectRole={setSelectedRole}
      />

      {selectedRole && (
        <>
          <div className="panel-card p-5">
            <h4 className="mb-4 text-sm font-medium text-stone-900 dark:text-stone-100">
              {t("agentConfig.selectAgentsForRole", {
                roleName: selectedRoleData?.name,
              })}
            </h4>
            <div className="grid gap-2.5">
              {availableAgents.map((agent) => (
                <label
                  key={agent.id}
                  className={`flex cursor-pointer items-center gap-3.5 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] p-3.5 transition-all duration-150 ${
                    currentRoleAgents.includes(agent.id)
                      ? "ring-2 ring-stone-500/30 dark:ring-stone-400/30"
                      : "hover:bg-[var(--theme-bg-card)]"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={currentRoleAgents.includes(agent.id)}
                    onChange={() => toggleAgent(agent.id)}
                    className="h-4.5 w-4.5 rounded border-[var(--theme-border)] text-stone-600 focus:ring-stone-500"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-stone-900 dark:text-stone-100 truncate">
                      {t(agent.name)}
                    </div>
                    <div className="text-xs text-stone-500 dark:text-stone-400 truncate mt-0.5 hidden sm:block">
                      {t(agent.description)}
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {hasChanges && (
            <div className="flex justify-end pt-2">
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="btn-primary flex items-center gap-2 px-5 py-2.5 text-sm disabled:opacity-50"
              >
                <Save size={16} />
                {t("common.save")}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
