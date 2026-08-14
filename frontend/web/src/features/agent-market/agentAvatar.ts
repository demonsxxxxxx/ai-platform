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
