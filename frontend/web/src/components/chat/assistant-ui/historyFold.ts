import type { Message } from "../../../types";
import type { V4PublicEvent } from "./publicEventAdapter";
import {
  createPublicV4ReducerState,
  reducePublicV4Event,
  type PublicV4ReducerBinding,
  type PublicV4ReducerState,
} from "./publicEventReducer";

export interface PublicV4HistoryFold {
  state: PublicV4ReducerState;
  acceptedEvents: number;
  rejectedEvents: number;
}

/**
 * Replay and hydration use the same reducer as live frames. Durable sequence
 * orders the replay, while duplicate and stale frames remain fail-closed.
 */
export function foldPublicV4History(
  events: readonly V4PublicEvent[],
  binding: PublicV4ReducerBinding,
  seedMessages: Message[] = [],
): PublicV4HistoryFold {
  const ordered = [...events].sort((left, right) => {
    if (left.sequence === null && right.sequence === null) return 0;
    if (left.sequence === null) return 1;
    if (right.sequence === null) return -1;
    return left.sequence - right.sequence;
  });
  let state = createPublicV4ReducerState(seedMessages, binding);
  let acceptedEvents = 0;
  let rejectedEvents = 0;
  for (const event of ordered) {
    const reduction = reducePublicV4Event(state, event);
    state = reduction.state;
    if (reduction.accepted) acceptedEvents += 1;
    else rejectedEvents += 1;
  }
  return { state, acceptedEvents, rejectedEvents };
}
