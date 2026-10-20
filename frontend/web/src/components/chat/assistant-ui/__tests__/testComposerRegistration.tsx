import { useLayoutEffect, useState } from "react";
import { usePlatformComposer } from "../platformComposer";

export interface TestComposerState {
  text: string;
  sendDisabled: boolean;
}

/**
 * Stands in for ChatInput's own registration so tests exercise the real composer
 * gate (`text`, `sendDisabled`) instead of a duplicated condition.
 */
export function useTestComposerRegistration({
  initial = { text: "", sendDisabled: false },
  onSend = () => {},
}: {
  initial?: TestComposerState;
  onSend?: (content: string) => void;
} = {}) {
  const { register } = usePlatformComposer();
  const [state, update] = useState(initial);
  useLayoutEffect(() => register({
    send: async (content: string) => { onSend(content); },
    requestCancel: () => {},
    getText: () => state.text,
    setText: (text) => update((current) => ({ ...current, text })),
    disabled: false,
    sendDisabled: state.sendDisabled,
    canCancel: false,
  }), [onSend, register, state.sendDisabled, state.text]);
  return {
    state,
    change: (next: Partial<TestComposerState>) => update((current) => ({ ...current, ...next })),
  };
}
