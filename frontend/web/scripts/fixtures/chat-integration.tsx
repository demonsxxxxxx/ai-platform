// Synthetic browser fixture: real ChatView/providers/renderers, no model or backend execution.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ToastHost } from "@lobehub/ui/base-ui";
import { ChatView } from "../../src/components/layout/AppContent/ChatView";
import { WorkbenchShell } from "../../src/components/workbench/WorkbenchShell";
import { ThemeProvider } from "../../src/contexts/ThemeContext";
import { LobeUiProvider } from "../../src/contexts/LobeUiProvider";
import { ModelCatalogProvider } from "../../src/contexts/ModelCatalogContext";
import { AuthProvider } from "../../src/hooks/useAuth";
import { useFileUpload } from "../../src/hooks/useFileUpload";
import { createSelectedSkillTaskState } from "../../src/hooks/useSelectedSkillTask";
import type { Message, MessageAttachment, MessagePart } from "../../src/types";
import "../../src/i18n";
import "katex/dist/katex.min.css";
import "../../src/styles/tailwind.css";
import "../../src/styles/base.css";
import "../../src/styles/animations.css";
import "../../src/styles/components.css";
import "../../src/styles/auth.css";
import "../../src/styles/chat.css";
import "../../src/styles/skill.css";
import "../../src/styles/skill-management-table.css";
import "../../src/styles/card-base.css";
import "../../src/styles/welcome.css";
import "../../src/styles/syntax-highlight.css";
import "../../src/styles/markdown.css";
import "../../src/styles/utilities.css";

const noop = () => {};
const ready = async () => {};
const toolParts: MessagePart[] = (["completed", "failed", "denied", "started"] as const).map((status, index) => ({
  type: "tool", id: `operation-${index}`, name: "private executor name", args: {},
  status, public_operation_id: `operation-${index}`, public_category: "read", duration_ms: 1250,
}));
const fixtureState = { sends: 0, stops: 0, advance: noop, finish: noop };
Object.assign(window, { __chatIntegration: fixtureState });

export function Fixture() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [running, setRunning] = useState(false);
  const [attachments, setAttachments] = useState<MessageAttachment[]>([]);
  const uploadControls = useFileUpload({ attachments, onAttachmentsChange: setAttachments, acceptedFileTypes: [] });
  useEffect(() => {
    fixtureState.advance = () => setMessages((current) => current.map((message) => message.role === "assistant"
      ? { ...message, content: "正在整理文件。", parts: [...toolParts, { type: "text", content: "正在整理文件。" }] } : message));
    fixtureState.finish = () => {
      setRunning(false);
      setMessages((current) => current.map((message) => message.role === "assistant"
        ? { ...message, isStreaming: false, content: "已整理文件。最终公开回答。", parts: [...toolParts, { type: "text", content: "已整理文件。最终公开回答。" }] } : message));
    };
  }, []);
  return <div style={{ height: "100dvh", display: "flex", overflow: "hidden" }}>
    <ChatView messages={messages} sessionId={null} conversationIdentityKey="synthetic-chat" currentRunId={running ? "run-synthetic" : null}
      canStopGeneration={running} isLoading={running} isLoadingHistory={false} canSendMessage
      agentEmptyProfile={{ agent_id: "agt_fixture", expected_revision: 1, name: "文件整理专家", description: "整理授权文件并汇总结果。", starter_prompts: ["请整理这批文件"], avatar_ref: "builtin:assistant", avatar_seed: "fixture", market_tags: [], completed_tasks: 0, is_favorite: false, published_at: "2026-01-01T00:00:00Z" }}
      tools={[]} onToggleTool={noop} onToggleCategory={noop} onToggleAll={noop} enabledToolsCount={0} totalToolsCount={0}
      skills={[]} taskSkills={[]} selectedSkillState={createSelectedSkillTaskState()} onSelectSkill={noop} onClearSelectedSkill={noop}
      onSelectedSkillRecoverable={ready} onSelectedSkillFilesReady={noop} skillsLoading={false} enabledSkillsCount={0} totalSkillsCount={0} enableSkills={false}
      agentOptions={{}} agentOptionValues={{}} onToggleAgentOption={noop} availableModels={[]} currentModelId="" onSelectModel={noop}
      onSendMessage={async (content) => {
        fixtureState.sends += 1;
        setMessages([
          { id: "user-synthetic", role: "user", content, timestamp: new Date(0) },
          { id: "assistant-synthetic", role: "assistant", content: "", timestamp: new Date(1), isStreaming: true, parts: toolParts },
        ]);
        setRunning(true);
        return { status: "accepted" };
      }}
      canRetryPendingSubmission={false} onRetryPendingSubmission={ready}
      onStopGeneration={async () => { fixtureState.stops += 1; fixtureState.finish(); return "acknowledged"; }}
      onReconnect={ready} onLoadHistory={ready} attachments={attachments} onAttachmentsChange={setAttachments} uploadControls={uploadControls}
      WorkbenchShellComponent={WorkbenchShell} />
  </div>;
}

createRoot(document.getElementById("root")!).render(
  <BrowserRouter><AuthProvider><ModelCatalogProvider><ThemeProvider><LobeUiProvider>
    <Fixture /><ToastHost />
  </LobeUiProvider></ThemeProvider></ModelCatalogProvider></AuthProvider></BrowserRouter>,
);
