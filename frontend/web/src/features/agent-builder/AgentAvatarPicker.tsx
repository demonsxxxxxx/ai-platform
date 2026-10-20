import { Button } from "@lobehub/ui/base-ui";
import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";

import { AgentIdentityAvatar } from "../../components/agent/AgentIdentityAvatar";
import { AGENT_AVATAR_STYLE_OPTIONS } from "../../components/agent/agentAvatar";
import type { AgentProfileAvatarRef } from "../../types/agentProfile";
import { uuid } from "../../utils/uuid";
import { LobeSelectField } from "../../components/lobe-ui/LobeSelectField";

const CANDIDATE_COUNT = 8;

function createCandidateSeeds(): string[] {
  return Array.from({ length: CANDIDATE_COUNT }, () => `expert-${uuid().slice(0, 8)}`);
}

export function AgentAvatarPicker({
  agentId,
  avatarRef,
  avatarSeed,
  disabled,
  name,
  onChange,
}: {
  agentId: string;
  avatarRef: AgentProfileAvatarRef;
  avatarSeed: string;
  disabled: boolean;
  name: string;
  onChange: (update: {
    avatarRef?: AgentProfileAvatarRef;
    avatarSeed?: string;
  }) => void;
}) {
  const [candidateSeeds, setCandidateSeeds] = useState(createCandidateSeeds);
  const effectiveSeed = avatarSeed.trim() || agentId;
  const visibleSeeds = useMemo(
    () => Array.from(new Set([effectiveSeed, ...candidateSeeds])).slice(0, CANDIDATE_COUNT),
    [candidateSeeds, effectiveSeed],
  );

  return (
    <fieldset className="min-w-0" data-agent-avatar-picker disabled={disabled}>
      <legend className="text-sm font-medium">头像风格</legend>
      <div className="mt-2 flex min-w-0 items-center gap-3">
        <AgentIdentityAvatar
          agentId={agentId}
          avatarRef={avatarRef}
          avatarSeed={effectiveSeed}
          name={name}
          size="sm"
        />
        <LobeSelectField
          aria-label="选择头像风格"
          className="min-h-10 min-w-0 flex-1"
          disabled={disabled}
          onChange={(value) => onChange({ avatarRef: value as AgentProfileAvatarRef })}
          value={avatarRef}
          options={AGENT_AVATAR_STYLE_OPTIONS.map((option) => ({
            value: option.ref,
            label: `${option.label} - ${option.description}`,
          }))}
        />
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="text-sm font-medium">选择头像</span>
        <Button type="default"
          disabled={disabled}
          icon={<RefreshCw aria-hidden="true" size={15} />}
          onClick={() => setCandidateSeeds(createCandidateSeeds())}
          title="生成一组新的头像候选"
          htmlType="button"
        >
          换一批
        </Button>
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2">
        {visibleSeeds.map((seed, index) => {
          const selected = seed === effectiveSeed;
          return (
            <Button type={selected ? "primary" : "default"}
              disabled={disabled}
              aria-label={selected ? `当前头像，第 ${index + 1} 个候选` : `选择第 ${index + 1} 个头像`}
              aria-pressed={selected}
              className="min-w-0"
              style={{ width: "100%", height: "auto", aspectRatio: "1", padding: 4 }}
              key={seed}
              onClick={() => onChange({ avatarSeed: seed })}
              htmlType="button"
              icon={<AgentIdentityAvatar
                agentId={agentId}
                avatarRef={avatarRef}
                avatarSeed={seed}
                name={name}
                size="sm"
              />}
            />
          );
        })}
      </div>
    </fieldset>
  );
}
