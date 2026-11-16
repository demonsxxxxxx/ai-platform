import { useTranslation } from "react-i18next";
import { CheckIcon, CircleIcon, Loader2Icon } from "lucide-react";
import { clsx } from "clsx";
import type { TodoItem } from "../../../../types";

type AssistantTodoStatus = "pending" | "active" | "done";

type AssistantTodoItem = {
  id: string;
  text: string;
  status: AssistantTodoStatus;
};

export interface TodoListProps {
  items: readonly TodoItem[];
  isStreaming?: boolean;
}

function toAssistantTodoItem(item: TodoItem, index: number): AssistantTodoItem {
  return {
    id: `${index}:${item.content}`,
    text: item.content,
    status:
      item.status === "completed"
        ? "done"
        : item.status === "in_progress"
          ? "active"
          : "pending",
  };
}

/**
 * TodoList adapted from the pinned assistant-ui TodoList element.
 * Platform validation stays in TodoBlock; this component only maps its
 * three admitted states to the source element's pending/active/done states.
 */
export function TodoList({ items, isStreaming }: TodoListProps) {
  const { t } = useTranslation();
  const sourceItems = items.map(toAssistantTodoItem);
  const done = sourceItems.filter((item) => item.status === "done").length;
  const total = sourceItems.length;
  const statusLabels: Record<AssistantTodoStatus, string> = {
    pending: t("runPlayback.status.pending"),
    active: t("runPlayback.status.running"),
    done: t("runPlayback.status.succeeded"),
  };

  return (
    <div
      data-slot="todo-list"
      className="my-1 flex w-full max-w-sm flex-col gap-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <span className="text-[13.5px] font-medium">
            {t("runPlayback.fields.progress")}
          </span>
        </div>
        <span className="shrink-0 whitespace-nowrap text-xs tabular-nums text-[var(--theme-text-secondary)]">
          {t("chat.todo.progress", { completed: done, total })}
        </span>
      </div>
      <ul className="flex flex-col gap-1">
        {sourceItems.map((item) => (
          <li
            key={item.id}
            data-status={item.status}
            className="flex items-start gap-2.5 py-0.5 text-[13.5px]"
          >
            <span
              aria-hidden="true"
              className="flex h-5 w-4 shrink-0 items-center justify-center"
            >
              {item.status === "done" ? (
                <span className="flex h-3.5 w-3.5 items-center justify-center rounded-[5px] border border-[var(--theme-border)] bg-[var(--theme-bg-hover)]">
                  <CheckIcon className="h-2.5 w-2.5 text-[var(--theme-text-secondary)]" />
                </span>
              ) : item.status === "active" ? (
                <Loader2Icon className="h-3.5 w-3.5 animate-spin text-[var(--theme-info-text)] motion-reduce:animate-none" />
              ) : (
                <CircleIcon className="h-3.5 w-3.5 text-[var(--theme-text-tertiary)]" />
              )}
            </span>
            <span className="sr-only">{statusLabels[item.status]}</span>
            <span
              className={clsx(
                "min-w-0 flex-1 break-words leading-5",
                item.status === "done" &&
                  "text-[var(--theme-text-secondary)] line-through decoration-[1.5px]",
                item.status === "active" && "text-[var(--theme-text)]",
                item.status === "pending" &&
                  "text-[var(--theme-text-secondary)]",
              )}
            >
              {item.text}
            </span>
          </li>
        ))}
      </ul>
      {isStreaming && (
        <span
          aria-hidden="true"
          className="sr-only"
        >
          {t("runPlayback.status.running")}
        </span>
      )}
    </div>
  );
}
