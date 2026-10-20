import { Button, PopoverRoot, PopoverPortal, PopoverPositioner, PopoverPopup } from "@lobehub/ui/base-ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Lightbulb, MessageCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  buildSelectionActionPrompt,
  dispatchSelectionActionPrompt,
  storePendingSelectionActionPrompt,
  type SelectionAction,
} from "./selectionActionPrompt";

interface PopoverState {
  selectedText: string;
  selectionRect: DOMRect;
}

function getNodeElement(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === Node.ELEMENT_NODE
    ? (node as Element)
    : node.parentElement;
}

function isSelectionInEditableArea(selection: Selection): boolean {
  const anchorElement = getNodeElement(selection.anchorNode);
  const focusElement = getNodeElement(selection.focusNode);
  return [anchorElement, focusElement].some((element) => {
    if (!element) return false;
    return !!element.closest(
      'input, textarea, select, button, [contenteditable="true"], [data-selection-action-popover]',
    );
  });
}

function getVisibleSelectionRect(range: Range): DOMRect | null {
  const rect = range.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) return rect;

  const firstRect = Array.from(range.getClientRects()).find(
    (clientRect) => clientRect.width > 0 && clientRect.height > 0,
  );
  return firstRect ?? null;
}

export function SelectionActionPopover() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [popover, setPopover] = useState<PopoverState | null>(null);
  const latestTextRef = useRef("");

  const hidePopover = useCallback(() => {
    setPopover(null);
    latestTextRef.current = "";
  }, []);

  const updateFromSelection = useCallback(() => {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
      hidePopover();
      return;
    }

    if (isSelectionInEditableArea(selection)) {
      hidePopover();
      return;
    }

    const selectedText = selection.toString().trim();
    if (!selectedText) {
      hidePopover();
      return;
    }

    const range = selection.getRangeAt(0);
    const selectionRect = getVisibleSelectionRect(range);
    if (!selectionRect) {
      hidePopover();
      return;
    }

    latestTextRef.current = selectedText;
    setPopover({ selectedText, selectionRect });
  }, [hidePopover]);

  useEffect(() => {
    let timer: number | null = null;
    const scheduleUpdate = (event: Event) => {
      if (timer !== null) window.clearTimeout(timer);
      if (event instanceof KeyboardEvent && event.key === "Escape") return;
      timer = window.setTimeout(updateFromSelection, 40);
    };

    document.addEventListener("mouseup", scheduleUpdate);
    document.addEventListener("keyup", scheduleUpdate);
    document.addEventListener("touchend", scheduleUpdate);
    window.addEventListener("resize", hidePopover);
    window.addEventListener("scroll", hidePopover, true);

    return () => {
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("mouseup", scheduleUpdate);
      document.removeEventListener("keyup", scheduleUpdate);
      document.removeEventListener("touchend", scheduleUpdate);
      window.removeEventListener("resize", hidePopover);
      window.removeEventListener("scroll", hidePopover, true);
    };
  }, [hidePopover, updateFromSelection]);

  const handleAction = (action: SelectionAction) => {
    const selectedText = latestTextRef.current || popover?.selectedText || "";
    if (!selectedText) return;

    const prompt = buildSelectionActionPrompt({
      action,
      selectedText,
      templates: {
        ask: t("selectionActions.askPrompt"),
        explain: t("selectionActions.explainPrompt"),
      },
    });

    if (location.pathname.startsWith("/chat")) {
      dispatchSelectionActionPrompt(prompt);
    } else {
      storePendingSelectionActionPrompt(prompt);
      navigate("/chat");
    }

    window.getSelection()?.removeAllRanges();
    hidePopover();
  };

  if (!popover) return null;

  return (
    <PopoverRoot open onOpenChange={(open) => { if (!open) hidePopover(); }}>
      <PopoverPortal>
        <PopoverPositioner anchor={{ getBoundingClientRect: () => popover.selectionRect }} placement="bottom" sideOffset={8}>
          <PopoverPopup data-selection-action-popover="true" initialFocus={false} finalFocus={false} className="flex gap-0.5 p-1" aria-label={t("selectionActions.ask")}>
            <Button type="text" size="small" htmlType="button"
              aria-label={t("selectionActions.ask")}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => handleAction("ask")}
            >
              <MessageCircle size={14} strokeWidth={1.5} />
              {t("selectionActions.ask")}
            </Button>
            <Button type="text" size="small" htmlType="button"
              aria-label={t("selectionActions.explain")}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => handleAction("explain")}
            >
              <Lightbulb size={14} strokeWidth={1.5} />
              {t("selectionActions.explain")}
            </Button>
          </PopoverPopup>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}
