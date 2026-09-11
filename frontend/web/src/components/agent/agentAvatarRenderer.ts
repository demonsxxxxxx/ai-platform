import { Avatar, Style } from "@dicebear/core";
import adventurer from "@dicebear/styles/adventurer.json";
import blobs from "@dicebear/styles/blobs.json";
import clay from "@dicebear/styles/clay.json";
import funEmoji from "@dicebear/styles/fun-emoji.json";
import icons from "@dicebear/styles/icons.json";
import lorelei from "@dicebear/styles/lorelei.json";
import micah from "@dicebear/styles/micah.json";
import personas from "@dicebear/styles/personas.json";
import pixelArt from "@dicebear/styles/pixel-art.json";
import planets from "@dicebear/styles/planets.json";
import shapes from "@dicebear/styles/shapes.json";
import voxelBot from "@dicebear/styles/voxel-bot.json";

import type { AgentProfileAvatarRef } from "../../types/agentProfile";

const DICEBEAR_STYLES = {
  "builtin:agent": new Style(lorelei),
  "builtin:assistant": new Style(voxelBot),
  "builtin:document": new Style(shapes),
  "builtin:research": new Style(micah),
  "builtin:cartoon": new Style(adventurer),
  "builtin:emoji": new Style(funEmoji),
  "builtin:pixel": new Style(pixelArt),
  "builtin:portrait": new Style(personas),
  "builtin:abstract": new Style(blobs),
  "builtin:planet": new Style(planets),
  "builtin:clay": new Style(clay),
  "builtin:icon": new Style(icons),
} satisfies Record<AgentProfileAvatarRef, Style>;

function opaqueSeed(value: string): string {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return `expert-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function renderAgentAvatar(seed: string, avatarRef: AgentProfileAvatarRef): string {
  return new Avatar(DICEBEAR_STYLES[avatarRef], { seed: opaqueSeed(seed) }).toDataUri();
}
