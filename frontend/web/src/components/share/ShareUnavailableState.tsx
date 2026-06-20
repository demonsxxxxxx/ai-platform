import { Ban, Clock, Lock, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

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
      <section className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
        <Icon className="mx-auto text-slate-500 dark:text-stone-300" size={32} />
        <h1 className="mt-4 text-lg font-semibold text-slate-900 dark:text-stone-50">
          {t(`share.unavailable.${reason}.title`)}
        </h1>
        <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
          {t(`share.unavailable.${reason}.description`)}
        </p>
      </section>
    </main>
  );
}
