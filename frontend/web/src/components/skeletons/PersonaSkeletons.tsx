import { SkeletonLine } from "./primitives";
import { PanelHeaderSkeleton } from "./PanelHeaderSkeleton";

export function PersonaPlazaSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 animate-fade-in">
      <PanelHeaderSkeleton hasSearch />
      <div className="skill-content-area flex-1 overflow-y-auto py-2 sm:py-4 px-4 sm:p-6">
        <div className="grid auto-grid-cols gap-4 sm:gap-5">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="scb">
              {/* Banner */}
              <div
                className="h-12 w-full shrink-0 relative"
                style={{
                  background: `linear-gradient(135deg, ${
                    [
                      "var(--theme-primary-light)",
                      "color-mix(in srgb, var(--theme-primary-light) 60%, var(--theme-bg))",
                      "var(--theme-bg-card)",
                    ][i % 3]
                  }, var(--theme-bg-card))`,
                }}
              />
              {/* Card body */}
              <div className="flex flex-1 flex-col -mt-3 pt-5 p-5">
                <div className="flex items-start gap-3">
                  <div className="scb__icon-ring shrink-0 skeleton-line" />
                  <div className="min-w-0 flex-1">
                    <SkeletonLine
                      width={i % 2 === 0 ? "w-3/4" : "w-1/2"}
                      className="!h-4"
                    />
                  </div>
                </div>
                <div className="mt-3 space-y-1.5">
                  <SkeletonLine width="w-full" className="!h-3" />
                  <SkeletonLine
                    width={i % 2 === 0 ? "w-5/6" : "w-2/3"}
                    className="!h-3"
                  />
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  <SkeletonLine width="w-14" className="!h-5 !rounded-full" />
                  <SkeletonLine width="w-20" className="!h-5 !rounded-full" />
                </div>
                {/* Footer buttons */}
                <div
                  className="mt-4 flex gap-2 border-t pt-3"
                  style={{ borderColor: "var(--theme-border)" }}
                >
                  <SkeletonLine width="w-16" className="!h-8 !rounded-lg" />
                  <SkeletonLine width="w-16" className="!h-8 !rounded-lg" />
                </div>
                <div className="flex-1" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function PersonaPageSkeleton() {
  return (
    <div className="flex h-full animate-fade-in">
      <PersonaPlazaSkeleton />
    </div>
  );
}
