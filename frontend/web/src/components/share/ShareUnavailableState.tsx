import { Ban, Clock, Lock, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { WorkbenchUnavailableState } from "../workbench/WorkbenchUnavailableState";

export type ShareUnavailableReason =
  | "denied"
  | "expired"
  | "revoked"
  | "unavailable";

const ICONS: Record<ShareUnavailableReason, React.ElementType> = {
  denied: Lock,
  expired: Clock,
  revoked: Ban,
  unavailable: ShieldAlert,
};

export interface ShareUnavailableStateProps {
  reason: ShareUnavailableReason;
}

export function ShareUnavailableState({ reason }: ShareUnavailableStateProps) {
  const { t } = useTranslation();
  const Icon = ICONS[reason];

  return (
    <main className="flex min-h-dvh items-center justify-center bg-slate-50 p-6 dark:bg-stone-950">
      <WorkbenchUnavailableState
        surface={`share-acl-create share-${reason}`}
        icon={Icon}
        title={t(`share.unavailable.${reason}.title`)}
        description={t(`share.unavailable.${reason}.description`)}
      />
    </main>
  );
}
