import type { ElementType } from "react";
import { ShieldAlert } from "lucide-react";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchUnavailableStateProps {
  title: string;
  description: string;
  icon?: ElementType;
  surface: string;
}

export function WorkbenchUnavailableState({
  title,
  description,
  icon: Icon = ShieldAlert,
  surface,
}: WorkbenchUnavailableStateProps) {
  return (
    <section
      data-workbench-unavailable
      data-fail-closed-surface={surface}
      className={`${workbenchSurface.compactPanel} mx-auto w-full max-w-xl p-5 text-center`}
    >
      <Icon className="mx-auto text-slate-500 dark:text-stone-300" size={32} />
      <h1 className="mt-4 text-base font-semibold text-slate-900 dark:text-stone-100">
        {title}
      </h1>
      <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
        {description}
      </p>
    </section>
  );
}
