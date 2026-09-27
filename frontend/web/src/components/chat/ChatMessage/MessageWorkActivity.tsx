import { clsx } from "clsx";
import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ChevronDown, ListTree } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MessagePart } from "../../../types";
import { isWorkActivityPart } from "./messagePartVisibility";

export function MessageWorkActivity({
  messageId,
  isStreaming,
  parts,
  partKeys,
  renderPart,
}: {
  messageId: string;
  isStreaming?: boolean;
  parts: MessagePart[];
  partKeys: string[];
  renderPart: (
    part: MessagePart,
    index: number,
    withinWorkDetails: boolean,
  ) => ReactNode;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(Boolean(isStreaming));
  const wasStreamingRef = useRef(Boolean(isStreaming));

  useEffect(() => {
    const isNowStreaming = Boolean(isStreaming);
    if (wasStreamingRef.current && !isNowStreaming) {
      setExpanded(false);
    }
    wasStreamingRef.current = isNowStreaming;
  }, [isStreaming]);

  const workActivityCount = parts.filter(isWorkActivityPart).length;
  const firstWorkActivityIndex = parts.findIndex(isWorkActivityPart);
  const workActivityRegionIds = parts.map((part, index) =>
    isWorkActivityPart(part) ? `chat-work-${messageId}-${index}` : "",
  );
  const controlledWorkActivityIds = workActivityRegionIds
    .filter(Boolean)
    .join(" ");

  return parts.map((part, index) => {
    const isWorkActivity = isWorkActivityPart(part);
    const renderedPart = renderPart(part, index, isWorkActivity);
    return (
      <Fragment key={partKeys[index] ?? `${messageId}:${index}`}>
        {index === firstWorkActivityIndex && (
          <button
            type="button"
            data-message-work-details-toggle
            aria-expanded={expanded}
            aria-controls={controlledWorkActivityIds}
            title={t(
              expanded
                ? "chat.workDetails.collapseAll"
                : "chat.workDetails.expandAll",
            )}
            onClick={() => setExpanded((current) => !current)}
            className="flex h-8 w-full max-w-2xl items-center gap-2 text-left text-sm font-medium text-[var(--theme-text-secondary)] transition-colors hover:text-[var(--theme-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--theme-primary)] focus-visible:ring-offset-2"
          >
            <ListTree size={15} aria-hidden="true" />
            <span>
              {t(
                isStreaming
                  ? "chat.workDetails.working"
                  : "chat.workDetails.title",
              )}
            </span>
            <span className="text-xs font-normal opacity-70">
              {t("chat.workDetails.itemCount", { count: workActivityCount })}
            </span>
            <span className="ml-auto text-xs font-normal">
              {t(
                expanded
                  ? "chat.workDetails.collapseAll"
                  : "chat.workDetails.expandAll",
              )}
            </span>
            <ChevronDown
              size={15}
              aria-hidden="true"
              className={clsx(
                "shrink-0 transition-transform duration-200",
                expanded && "rotate-180",
              )}
            />
          </button>
        )}
        {isWorkActivity ? (
          <div id={workActivityRegionIds[index]} hidden={!expanded}>
            {renderedPart}
          </div>
        ) : (
          renderedPart
        )}
      </Fragment>
    );
  });
}
