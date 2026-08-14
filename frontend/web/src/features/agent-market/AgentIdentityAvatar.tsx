import { useMemo } from "react";

import { buildAgentAvatarUrl } from "./agentAvatar";

export function AgentIdentityAvatar({
  agentId,
  avatarSeed,
  name,
  size = "md",
}: {
  agentId: string;
  avatarSeed?: string;
  name: string;
  size?: "sm" | "md" | "lg";
}) {
  const source = useMemo(
    () => buildAgentAvatarUrl(avatarSeed?.trim() || agentId),
    [agentId, avatarSeed],
  );
  const dimensions = size === "lg" ? "h-16 w-16" : size === "sm" ? "h-10 w-10" : "h-12 w-12";

  return (
    <span
      aria-label={`${name} 头像`}
      className={`${dimensions} inline-flex shrink-0 overflow-hidden rounded-full border border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] shadow-sm`}
      role="img"
    >
      <img
        alt=""
        className="h-full w-full object-cover"
        loading="lazy"
        src={source}
      />
    </span>
  );
}
