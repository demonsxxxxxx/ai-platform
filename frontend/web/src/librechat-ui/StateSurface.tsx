import type { ElementType, ReactNode } from "react";
import { Dot } from "lucide-react";
import { clsx } from "clsx";

export type LibreChatGovernanceState =
  | "logged-out"
  | "loading"
  | "no-workspace"
  | "forbidden"
  | "degraded"
  | "ready";

export interface LibreChatStateSurfaceProps {
  state: LibreChatGovernanceState;
  title: ReactNode;
  description?: ReactNode;
  icon?: ElementType;
  iconClassName?: string;
  surface: string;
  actions?: ReactNode;
  details?: ReactNode[];
  smokeAttributes?: Record<string, string>;
  className?: string;
  children?: ReactNode;
}

/** Renders governed loading, forbidden, degraded, and ready state surfaces. */
export function LibreChatStateSurface({
  state,
  title,
  description,
  icon: Icon,
  iconClassName,
  surface,
  actions,
  details,
  smokeAttributes,
  className,
  children,
}: LibreChatStateSurfaceProps) {
  return (
    <section
      data-librechat-state-surface
      data-workbench-state-surface
      data-librechat-ui-state={state}
      data-fail-closed-surface={surface}
      {...smokeAttributes}
      className={clsx(
        "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 text-center shadow-[0_1px_2px_rgba(18,38,63,0.04)]",
        className,
      )}
    >
      {Icon ? (
        <div className="mx-auto flex size-11 items-center justify-center rounded-lg bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] ring-1 ring-[var(--theme-border)]">
          <Icon className={iconClassName} size={22} strokeWidth={1.9} />
        </div>
      ) : null}
      <h1 className="mt-4 text-base font-semibold text-[var(--theme-text)]">
        {title}
      </h1>
      {description ? (
        <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[var(--theme-text-secondary)]">
          {description}
        </p>
      ) : null}
      {details && details.length > 0 ? (
        <div className="mx-auto mt-5 grid max-w-md gap-2 text-left">
          {details.map((detail, index) => (
            <div
              key={index}
              data-workbench-state-detail
              className="flex items-start gap-2 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-3 text-xs leading-5 text-[var(--theme-text-secondary)]"
            >
              <Dot
                size={18}
                strokeWidth={3}
                className="mt-0.5 shrink-0 text-[var(--theme-text-tertiary)]"
              />
              <span>{detail}</span>
            </div>
          ))}
        </div>
      ) : null}
      {children}
      {actions ? <div className="mt-4 flex justify-center">{actions}</div> : null}
    </section>
  );
}
