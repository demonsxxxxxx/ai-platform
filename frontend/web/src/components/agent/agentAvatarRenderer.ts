import { Avatar, Style } from "@dicebear/core";
import lorelei from "@dicebear/styles/lorelei.json";
import micah from "@dicebear/styles/micah.json";
import shapes from "@dicebear/styles/shapes.json";
import voxelBot from "@dicebear/styles/voxel-bot.json";

import type { AgentProfileAvatarRef } from "../../types/agentProfile";

const DICEBEAR_STYLES = {
  "builtin:agent": new Style(lorelei),
  "builtin:assistant": new Style(voxelBot),
  "builtin:document": new Style(shapes),
  "builtin:research": new Style(micah),
} satisfies Record<AgentProfileAvatarRef, Style>;

function opaqueSeed(value: string): string {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `agent-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function renderAgentAvatar(seed: string, avatarRef: AgentProfileAvatarRef): string {
  return new Avatar(DICEBEAR_STYLES[avatarRef], { seed: opaqueSeed(seed) }).toDataUri();
}
