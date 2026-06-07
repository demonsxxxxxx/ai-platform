import { ShieldAlert } from "lucide-react";

export function QuarantinedLegacyPanel() {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-md rounded-lg border border-stone-200 bg-white p-5 text-center shadow-sm dark:border-stone-700 dark:bg-stone-900">
        <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
          <ShieldAlert size={20} strokeWidth={1.8} />
        </div>
        <h2 className="text-sm font-semibold text-stone-900 dark:text-stone-100">
          Legacy surface quarantined
        </h2>
        <p className="mt-2 text-sm leading-6 text-stone-500 dark:text-stone-400">
          This admin surface is disabled until it is remapped to ai-platform
          public or same-tenant admin projections.
        </p>
      </div>
    </div>
  );
}
