import { Button, ModalRoot, ModalPortal, ModalBackdrop, ModalPopup, ModalHeader, ModalTitle, ModalContent } from "@lobehub/ui/base-ui";
import { X } from "lucide-react";

interface AgentBuilderDialogProps {
  isOpen: boolean;
  title: string;
  descriptionId?: string;
  onClose: () => void;
  children: React.ReactNode;
}

/** Agent configuration content, with library-managed focus, dismissal and scroll lock. */
export function AgentBuilderDialog({ isOpen, title, descriptionId, onClose, children }: AgentBuilderDialogProps) {
  return (
    <ModalRoot open={isOpen} onOpenChange={(open) => { if (!open) onClose(); }}>
      <ModalPortal>
        <ModalBackdrop />
        <ModalPopup width="min(42rem, calc(100vw - 2rem))" aria-describedby={descriptionId}>
          <ModalHeader>
            <ModalTitle>{title}</ModalTitle>
            <Button type="text" aria-label={`关闭${title}`} onClick={onClose} autoFocus icon={<X size={17} aria-hidden="true" />} />
          </ModalHeader>
          <ModalContent className="min-h-0 overflow-y-auto">{children}</ModalContent>
        </ModalPopup>
      </ModalPortal>
    </ModalRoot>
  );
}
