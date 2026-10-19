import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AssistantRuntimeProvider, MessageNotSentError, ThreadPrimitive } from "@assistant-ui/react";
import { PlatformComposerContext, type PlatformComposerBinding } from "./platformComposer";
import type { Message } from "../../../types";
import {
  useAssistantUiExternalStoreRuntime,
  type AssistantUiRuntimeActions,
} from "./externalStoreRuntime";

export interface AssistantUiProjectionProps {
  messages: readonly Message[];
  isRunning: boolean;
  actions: AssistantUiRuntimeActions;
  children: ReactNode;
}

/** Connect assistant-ui interactions to platform messages, submission and draft authority. */
export function AssistantUiProjection({
  messages,
  isRunning,
  actions,
  children,
}: AssistantUiProjectionProps) {
  const bindingRef = useRef<PlatformComposerBinding | null>(null);
  const [gates, setGates] = useState({ disabled: false, sendDisabled: true });
  const register = useCallback((binding: PlatformComposerBinding) => {
    bindingRef.current = binding;
    setGates((current) => current.disabled === binding.disabled && current.sendDisabled === binding.sendDisabled
      ? current : { disabled: binding.disabled, sendDisabled: binding.sendDisabled });
    return () => { if (bindingRef.current === binding) bindingRef.current = null; };
  }, []);
  const composerContext = useMemo(() => ({ register }), [register]);
  const platformActions = useMemo<AssistantUiRuntimeActions>(() => ({
    ...actions,
    sendMessage: async (content) => {
      const binding = bindingRef.current;
      if (!binding || binding.disabled || binding.sendDisabled) throw new MessageNotSentError();
      return binding.send(content);
    },
    cancel: async () => {
      const binding = bindingRef.current;
      if (binding?.canCancel) binding.requestCancel();
    },
  }), [actions]);
  const runtime = useAssistantUiExternalStoreRuntime(messages, isRunning, platformActions, gates);
  useEffect(() => runtime.thread.composer.subscribe(() => {
    const text = runtime.thread.composer.getState().text;
    const binding = bindingRef.current;
    // Native send clears optimistically; platform receipts own clearing the persisted draft.
    if (text && binding && text !== binding.getText()) binding.setText(text);
  }), [runtime]);
  return (
    <PlatformComposerContext.Provider value={composerContext}>
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root asChild>
        <div
          className="min-h-0 flex flex-1 flex-col overflow-hidden"
          data-assistant-ui-projection
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
        >{children}</div>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
    </PlatformComposerContext.Provider>
  );
}
