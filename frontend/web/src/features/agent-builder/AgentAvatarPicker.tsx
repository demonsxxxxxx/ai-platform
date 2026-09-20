import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";

import { AgentIdentityAvatar } from "../../components/agent/AgentIdentityAvatar";
import { AGENT_AVATAR_STYLE_OPTIONS } from "../../components/agent/agentAvatar";
import type { AgentProfileAvatarRef } from "../../types/agentProfile";
import { uuid } from "../../utils/uuid";

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
        <select
          aria-label="选择头像风格"
          className="min-h-10 min-w-0 flex-1 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)]"
          onChange={(event) => onChange({ avatarRef: event.currentTarget.value as AgentProfileAvatarRef })}
          value={avatarRef}
        >
          {AGENT_AVATAR_STYLE_OPTIONS.map((option) => (
            <option key={option.ref} value={option.ref}>
              {option.label} - {option.description}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="text-sm font-medium">选择头像</span>
        <button
          className="btn-secondary inline-flex min-h-10 cursor-pointer items-center gap-2 px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          onClick={() => setCandidateSeeds(createCandidateSeeds())}
          title="生成一组新的头像候选"
          type="button"
        >
          <RefreshCw aria-hidden="true" size={15} />
          换一批
        </button>
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2 sm:grid-cols-8">
        {visibleSeeds.map((seed, index) => {
          const selected = seed === effectiveSeed;
          return (
            <button
              aria-label={selected ? `当前头像，第 ${index + 1} 个候选` : `选择第 ${index + 1} 个头像`}
              aria-pressed={selected}
              className={`flex aspect-square min-w-0 cursor-pointer items-center justify-center rounded-md border p-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] disabled:cursor-not-allowed disabled:opacity-60 ${
                selected
                  ? "border-[var(--theme-primary)] bg-[var(--theme-primary-light)]/40"
                  : "border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] hover:bg-[var(--theme-bg-sidebar)]"
              }`}
              key={seed}
              onClick={() => onChange({ avatarSeed: seed })}
              type="button"
            >
              <AgentIdentityAvatar
                agentId={agentId}
                avatarRef={avatarRef}
                avatarSeed={seed}
                name={name}
                size="sm"
              />
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
