import type { TodoItem } from "../../../types";
import { TodoList } from "./elements/TodoList";

const PUBLIC_TODO_STATUSES = new Set(["pending", "in_progress", "completed"]);

function isPublicTodoItem(item: TodoItem): boolean {
  return Boolean(
    item &&
      typeof item.content === "string" &&
      item.content.trim() &&
      PUBLIC_TODO_STATUSES.has(item.status),
  );
}

interface TodoBlockProps {
  items: TodoItem[];
  isStreaming?: boolean;
}

/** Adapts the existing todo event contract to the pinned TodoList element. */
export function TodoBlock({ items, isStreaming }: TodoBlockProps) {
  const publicItems = items.filter(isPublicTodoItem);
  if (publicItems.length === 0) return null;
  return <TodoList items={publicItems} isStreaming={isStreaming} />;
}
