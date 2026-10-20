import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AssistantRuntimeProvider, MessageNotSentError, type AttachmentAdapter, type ExternalStoreThreadListAdapter, type ThreadSuggestion } from "@assistant-ui/react";
import { Thread } from "./elements/thread";
import { PlatformComposerContext, type PlatformComposerBinding } from "./platformComposer";
import type { Message } from "../../../types";
import {
  useAssistantUiExternalStoreRuntime,
  type AssistantUiRuntimeActions,
} from "./externalStoreRuntime";

const EMPTY_SUGGESTIONS: readonly ThreadSuggestion[] = [];

export interface AssistantUiProjectionProps {
  messages: readonly Message[];
  isRunning: boolean;
  actions: AssistantUiRuntimeActions;
  threadListAdapter?: ExternalStoreThreadListAdapter;
  /** Only model-generated questions for the settled answer; empty means none. */
  suggestions?: readonly ThreadSuggestion[];
  children: ReactNode;
}

/** Connect assistant-ui interactions to platform messages, submission and draft authority. */
export function AssistantUiProjection({
  messages,
  isRunning,
  actions,
  threadListAdapter,
  suggestions = EMPTY_SUGGESTIONS,
  children,
}: AssistantUiProjectionProps) {
  const bindingRef = useRef<PlatformComposerBinding | null>(null);
  const [gates, setGates] = useState<{ disabled: boolean; sendDisabled: boolean; attachmentAdapter?: AttachmentAdapter }>({ disabled: false, sendDisabled: true });
  const register = useCallback((binding: PlatformComposerBinding) => {
    bindingRef.current = binding;
    setGates((current) => current.disabled === binding.disabled && current.sendDisabled === binding.sendDisabled && current.attachmentAdapter === binding.attachmentAdapter
      ? current : { disabled: binding.disabled, sendDisabled: binding.sendDisabled, attachmentAdapter: binding.attachmentAdapter });
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
  const runtime = useAssistantUiExternalStoreRuntime(messages, isRunning, platformActions, gates, threadListAdapter, gates.attachmentAdapter, suggestions);
  useEffect(() => runtime.thread.composer.subscribe(() => {
    const text = runtime.thread.composer.getState().text;
    const binding = bindingRef.current;
    // Native send clears optimistically; platform receipts own clearing the persisted draft.
    if (text && binding && text !== binding.getText()) binding.setText(text);
  }), [runtime]);
  return (
    <PlatformComposerContext.Provider value={composerContext}>
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread>{children}</Thread>
    </AssistantRuntimeProvider>
    </PlatformComposerContext.Provider>
  );
}
