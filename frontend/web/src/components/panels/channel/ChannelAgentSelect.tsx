/**
 * Agent selector for channel configuration.
 * Fetches user's available agents and renders a select dropdown.
 */
import { useState, useEffect } from "react";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import { agentApi } from "../../../services/api/agent";
import type { AgentInfo } from "../../../types";
import { GlassSelect } from "../../common/GlassSelect";

interface ChannelAgentSelectProps {
  value: string | null | undefined;
  onChange: (agentId: string | null) => void;
}

export function ChannelAgentSelect({
  value,
  onChange,
}: ChannelAgentSelectProps) {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    agentApi
      .list()
      .then((res) => {
        setAgents(res.agents || []);
      })
      .catch(() => {
        setAgents([]);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-stone-700 dark:text-stone-300">
        <div className="flex items-center gap-1.5">
          <Bot size={14} />
          {t("channel.agent", "Agent")}
        </div>
      </label>
      <GlassSelect
        value={value || ""}
        onChange={(v) => onChange(v || null)}
        disabled={loading}
        placeholder={
          loading
            ? t("common.loading", "Loading...")
            : t("channel.defaultAgent", "Default Agent")
        }
        options={agents.map((agent) => ({
          value: agent.id,
          label: `${t(agent.name)} — ${t(agent.description)}`,
        }))}
      />
      <p className="text-xs text-stone-500 dark:text-stone-500">
        {t(
          "channel.agentHint",
          "Select which agent handles messages from this channel",
        )}
      </p>
    </div>
  );
}
