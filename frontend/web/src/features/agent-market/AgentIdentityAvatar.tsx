import { useMemo } from "react";
import { Avatar, Style } from "@dicebear/core";
import lorelei from "@dicebear/styles/lorelei.json";

const DICEBEAR_STYLE = new Style(lorelei);

function opaqueSeed(value: string): string {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `agent-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function buildAgentAvatarUrl(seed: string): string {
  return new Avatar(DICEBEAR_STYLE, { seed: opaqueSeed(seed) }).toDataUri();
}

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
