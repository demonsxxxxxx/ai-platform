import {
  Bot,
  Brain,
  FileText,
  Layers,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import type {
  ComposerSelectionToken,
  ComposerSelectionTokenType,
} from "./slashCommand";

interface ComposerSelectionChipsProps {
  tokens: ComposerSelectionToken[];
  onRemove: (token: ComposerSelectionToken) => void;
}

const TOKEN_META: Record<
  ComposerSelectionTokenType,
  {
    label: string;
    Icon: React.ElementType;
  }
> = {
  skill: { label: "Skill", Icon: Sparkles },
  mcp: { label: "MCP", Icon: Wrench },
  agent: { label: "Agent", Icon: Bot },
  model: { label: "Model", Icon: Brain },
  file: { label: "File", Icon: FileText },
  context: { label: "Context", Icon: Layers },
};

export function ComposerSelectionChips({
  tokens,
  onRemove,
}: ComposerSelectionChipsProps) {
  if (tokens.length === 0) return null;

  return (
    <div className="composer-token-row" aria-label="Selected composer context">
      {tokens.map((token) => {
        const { Icon, label } = TOKEN_META[token.type];
        const unavailable = token.state === "unavailable";
        return (
          <span
            key={`${token.type}:${token.id}`}
            className="composer-token-chip"
            data-unavailable={unavailable ? "" : undefined}
            title={token.description}
          >
            <Icon size={14} aria-hidden="true" />
            <span className="composer-token-type">{label}</span>
            <span className="composer-token-label">{token.label}</span>
            <button
              type="button"
              className="composer-token-remove"
              aria-label={`Remove ${label} ${token.label}`}
              onClick={() => onRemove(token)}
            >
              <X size={13} />
            </button>
          </span>
        );
      })}
    </div>
  );
}
