import { type ReactNode } from "react";
import { Checkbox } from "./Checkbox";

export interface SkillBaseCardProps {
  title: string;
  description?: string;
  descriptionMaxLines?: 2 | 3;
  bannerOverlay?: ReactNode;
  icon?: ReactNode;
  statusPills?: ReactNode;
  tags?: ReactNode;
  meta?: ReactNode;
  extraContent?: ReactNode;
  footer?: ReactNode;
  muted?: boolean;
  selected?: boolean;
  selectionMode?: boolean;
  onSelect?: () => void;
  animated?: boolean;
  animationDelay?: number;
  className?: string;
  onClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export function SkillBaseCard({
  title,
  description,
  descriptionMaxLines = 2,
  bannerOverlay,
  icon,
  statusPills,
  tags,
  meta,
  extraContent,
  footer,
  muted = false,
  selected = false,
  selectionMode = false,
  onSelect,
  animated = false,
  animationDelay = 0,
  className = "",
  onClick,
}: SkillBaseCardProps) {
  const lineClamp = descriptionMaxLines === 3 ? "line-clamp-3" : "line-clamp-2";

  return (
    <div
      className={`scb group flex h-full flex-col overflow-hidden rounded-lg bg-[var(--theme-workbench-panel)] shadow-sm dark:shadow-none dark:border dark:border-[var(--theme-border)] ${
        muted ? "scb--muted" : ""
      } ${
        selected
          ? "ring-2 ring-[var(--theme-primary)] animate-[select-glow_2s_ease-in-out]"
          : ""
      } ${animated ? "scb--animated" : ""} ${
        selectionMode && onSelect ? "cursor-pointer" : ""
      } ${className}`}
      style={animated ? { animationDelay: `${animationDelay}ms` } : undefined}
      onClick={
        selectionMode && onSelect
          ? (e) => {
              if (
                !(e.target as HTMLElement).closest("button") &&
                !(e.target as HTMLElement).closest('[role="checkbox"]')
              ) {
                onSelect();
              }
            }
          : onClick
      }
    >
      {selectionMode && onSelect && (
        <div
          className={`absolute top-3 right-3 z-10 transition-all duration-200 ${
            selected ? "scale-110" : "sm:scale-90 sm:group-hover:scale-100"
          }`}
        >
          <Checkbox
            size="lg"
            checked={selected}
            onChange={() => onSelect()}
            className="shadow-sm sm:opacity-0 sm:group-hover:opacity-100"
          />
        </div>
      )}

      <div className="flex flex-1 flex-col p-3.5 sm:p-4">
        <div className="flex items-start gap-3">
          {icon && <div className="scb__icon-ring shrink-0">{icon}</div>}
          <div className="min-w-0 flex-1">
            <h3
              className="truncate text-sm font-semibold text-[var(--theme-text)] leading-tight"
              title={title}
            >
              {title}
            </h3>
            {statusPills}
          </div>
          {bannerOverlay && (
            <div className="ml-auto flex shrink-0 gap-1.5">{bannerOverlay}</div>
          )}
        </div>

        {description && (
          <p
            className={`mt-2.5 text-xs leading-5 text-[var(--theme-text-secondary)] ${lineClamp} min-h-[2.5rem]`}
          >
            {description}
          </p>
        )}

        {tags && <div className="mt-2.5">{tags}</div>}

        {extraContent && <div className="mt-2.5">{extraContent}</div>}

        <div className="flex-1" />

        {meta && <div className="mt-3">{meta}</div>}

        {footer && <div className="scb__footer">{footer}</div>}
      </div>
    </div>
  );
}
