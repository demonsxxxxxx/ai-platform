import { useEffect, useState } from "react";

import type { AgentProfileAvatarRef } from "../../types/agentProfile";
import { buildAgentAvatarUrl } from "./agentAvatar";

export function AgentIdentityAvatar({
  agentId,
  avatarRef = "builtin:agent",
  avatarSeed,
  name,
  size = "md",
}: {
  agentId: string;
  avatarRef?: AgentProfileAvatarRef;
  avatarSeed?: string;
  name: string;
  size?: "sm" | "md" | "lg";
}) {
  const [source, setSource] = useState<string | null>(null);
  const seed = avatarSeed?.trim() || agentId;
  const dimensions =
    size === "lg" ? "h-16 w-16" : size === "sm" ? "h-10 w-10" : "h-12 w-12";

  useEffect(() => {
    let active = true;
    setSource(null);
    void buildAgentAvatarUrl(seed, avatarRef)
      .then((nextSource) => {
        if (active) setSource(nextSource);
      })
      .catch(() => {
        if (active) setSource(null);
      });
    return () => {
      active = false;
    };
  }, [avatarRef, seed]);

  return (
    <span
      aria-label={`${name} 头像`}
      className={`${dimensions} inline-flex shrink-0 overflow-hidden rounded-full border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] shadow-sm`}
      data-agent-avatar-ref={avatarRef}
      role="img"
    >
      {source ? (
        <img alt="" className="h-full w-full object-cover" loading="lazy" src={source} />
      ) : (
        <span
          aria-hidden="true"
          className="flex h-full w-full items-center justify-center text-sm font-semibold text-[var(--theme-text-secondary)]"
        >
          {(name.trim() || "E").slice(0, 1).toUpperCase()}
        </span>
      )}
    </span>
  );
}
