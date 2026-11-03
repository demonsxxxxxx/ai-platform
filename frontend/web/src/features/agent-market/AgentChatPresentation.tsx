import type { ReactNode } from "react";
import { ThreadPrimitive } from "@assistant-ui/react";
import { Button } from "@lobehub/ui/base-ui";
import { AgentIdentityAvatar } from "../../components/agent/AgentIdentityAvatar";
import type { AgentProfilePublicProjection } from "../../types/agentProfile";

export function AgentChatHeader({ profile }: { profile: AgentProfilePublicProjection }) {
  return (
    <div data-expert-workspace-header className="flex min-w-0 items-center gap-3">
      <AgentIdentityAvatar
        agentId={profile.agent_id}
        avatarRef={profile.avatar_ref}
        avatarSeed={profile.avatar_seed}
        name={profile.name}
        size="sm"
      />
      <div className="min-w-0">
        <h1 className="truncate text-[15px] font-semibold leading-5 text-[var(--theme-text)]" data-expert-workspace-name>
          {profile.name}
        </h1>
        {profile.description ? (
          <p className="truncate text-xs leading-4 text-[var(--theme-text-secondary)]" data-expert-workspace-description title={profile.description}>
            {profile.description}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function AgentChatOpening({ profile, composer }: { profile: AgentProfilePublicProjection; composer: ReactNode }) {
  return (
    <div
      className="welcome-root welcome-chat-start relative flex h-full min-h-0 flex-col overflow-y-auto py-3"
      data-agent-chat-opening
      data-workbench-empty-state="agent-chat"
    >
      <section className="chat-start-surface flex w-full max-w-[var(--chat-column-max)] flex-1 flex-col items-center gap-4">
        {/* Published copy is presentation only, never a synthetic runtime message. */}
        {profile.description ? (
          <p className="max-w-2xl whitespace-pre-wrap text-center text-sm leading-7 text-[var(--theme-text-secondary)]" data-agent-opening data-agent-opening-message>
            {profile.description}
          </p>
        ) : <div data-agent-opening />}
        <div className="mt-auto w-full pt-8">{composer}</div>
      </section>
    </div>
  );
}

export function AgentStarterPrompts({ profile, disabled }: { profile: AgentProfilePublicProjection; disabled: boolean }) {
  if (!profile.starter_prompts.length) return null;
  return (
    <div className="mx-auto mb-3 flex max-w-[var(--chat-column-max)] flex-wrap gap-2 px-2" data-agent-starter-prompts>
      <p className="w-full text-xs font-medium text-[var(--theme-text-secondary)]">可以直接开始的任务</p>
      {profile.starter_prompts.map((prompt) => (
        <ThreadPrimitive.Suggestion key={prompt} prompt={prompt} send={false} asChild>
          <Button type="default"
            style={{ height: "auto", padding: "8px 12px", textAlign: "left", whiteSpace: "normal" }}
            disabled={disabled} htmlType="button">
            {prompt}
          </Button>
        </ThreadPrimitive.Suggestion>
      ))}
    </div>
  );
}
