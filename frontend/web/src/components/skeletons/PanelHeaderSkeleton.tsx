import { SkeletonLine } from "./primitives";

/** Matches PanelHeader layout: icon box (size-12, gradient bg) + title + optional search + actions */
export function PanelHeaderSkeleton({
  hasSearch = true,
}: {
  hasSearch?: boolean;
}) {
  return (
    <div className="panel-header">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          {/* Icon box — matches real PanelHeader: size-12 rounded-xl gradient + shadow + ring */}
          <div
            className="flex size-12 flex-shrink-0 items-center justify-center rounded-xl shadow-sm ring-1 ring-stone-200/60 dark:ring-stone-700/50"
            style={{
              background:
                "linear-gradient(to bottom right, var(--theme-bg-card), color-mix(in srgb, var(--theme-bg) 80%, white))",
            }}
          >
            <div className="skeleton-line size-6 rounded-md" />
          </div>
          <div className="min-w-0">
            <SkeletonLine
              width="w-28 sm:w-36 xl:w-48"
              className="!h-[22px] sm:!h-6"
            />
            <SkeletonLine
              width="w-40 sm:w-52 xl:w-64"
              className="!h-3.5 sm:!h-4 mt-0.5"
            />
          </div>
        </div>
        <div className="flex flex-nowrap flex-shrink-0 items-center gap-1.5 sm:gap-2">
          <div className="skeleton-line h-9 w-20 sm:w-24 xl:w-28 rounded-lg" />
          <div className="skeleton-line h-9 w-9 rounded-lg sm:hidden" />
          <div className="skeleton-line h-9 w-20 sm:w-24 xl:w-28 rounded-lg hidden sm:block" />
        </div>
      </div>
      {hasSearch && (
        <div className="mt-2 flex items-center gap-2 sm:mt-3">
          <div className="skeleton-line h-10 flex-1 rounded-lg" />
        </div>
      )}
    </div>
  );
}
