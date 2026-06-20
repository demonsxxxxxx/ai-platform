import { SkeletonLine } from "./primitives";
import { SidebarSkeleton } from "./SidebarSkeleton";

/** Full chat page skeleton: sidebar + header + welcome */
export function ChatPageSkeleton() {
  return (
    <div
      className="flex h-[100dvh] w-full overflow-hidden animate-fade-in"
      style={{ backgroundColor: "var(--theme-bg)" }}
    >
      <SidebarSkeleton />

      {/* Main area */}
      <div className="relative flex flex-1 min-w-0 flex-col overflow-hidden">
        {/* Header skeleton — matches real Header layout */}
        <header
          className="relative z-50 flex items-center px-3 sm:px-5 pb-1 shrink-0"
          style={{ paddingTop: "max(0.75rem, env(safe-area-inset-top))" }}
        >
          <div className="flex items-center gap-2 flex-shrink-0">
            {/* Mobile hamburger */}
            <div className="skeleton-line size-8 rounded-lg sm:hidden" />
            {/* Model selector — text button style */}
            <div className="hidden sm:flex items-center gap-1.5">
              <SkeletonLine width="w-28 sm:w-36" className="!h-5 !rounded-md" />
              <div className="skeleton-line size-4 rounded-sm" />
            </div>
          </div>
          <div className="flex-1" />
          <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
            {/* More menu */}
            <div className="skeleton-line size-8 rounded-lg" />
            {/* UserMenu avatar */}
            <div className="skeleton-line size-8 rounded-lg" />
          </div>
        </header>

        {/* Welcome skeleton */}
        <main className="flex-1 overflow-hidden">
          <WelcomeSkeleton />
        </main>
      </div>
    </div>
  );
}

/** Shared user message skeleton block */
function UserMessageSkeleton({
  msg,
}: {
  msg: { bubble: string; lines: string[] };
}) {
  return (
    <div className="w-full px-2 py-3 sm:py-4 sm:px-4 group">
      <div className="mx-auto flex max-w-3xl lg:max-w-4xl xl:max-w-5xl justify-end px-2">
        <div
          className={`flex flex-col items-stretch max-w-[90%] ${msg.bubble}`}
        >
          <div
            className="rounded-2xl w-full px-4 py-2 border"
            style={{
              background:
                "color-mix(in srgb, var(--theme-text-secondary) 8%, var(--theme-bg-card))",
              borderColor: "var(--theme-border)",
            }}
          >
            <div className="leading-relaxed text-[15px] sm:text-base space-y-1.5">
              {msg.lines.map((w, li) => (
                <SkeletonLine key={li} width={w} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Shared assistant message skeleton block */
function AssistantMessageSkeleton() {
  return (
    <div className="group w-full animate-[fade-in_0.3s_ease-out] scroll-mt-6 rounded-2xl">
      <div className="mx-auto flex flex-col max-w-3xl lg:max-w-4xl xl:max-w-5xl px-4 sm:px-6">
        {/* Avatar + name */}
        <div className="mb-3 flex items-center gap-2">
          <div className="skeleton-line size-6 rounded-full shrink-0" />
          <SkeletonLine
            width="w-16 sm:w-20"
            className="!h-[18px] sm:!h-[20px]"
          />
        </div>
        {/* Response content skeleton */}
        <div className="min-w-0 min-h-0 py-1 sm:py-2">
          <div className="space-y-3 px-2 my-2">
            <div className="skeleton-line w-full h-2 sm:h-[7px] rounded-full" />
            <div className="flex gap-2 sm:gap-3">
              <div className="skeleton-line flex-1 h-2 sm:h-[7px] rounded-full" />
              <div className="skeleton-line flex-1 h-2 sm:h-[7px] rounded-full" />
              <div className="skeleton-line w-2/5 h-2 sm:h-[7px] rounded-full hidden sm:block" />
            </div>
            <div className="flex gap-2 sm:gap-3">
              <div className="skeleton-line flex-1 h-2 sm:h-[7px] rounded-full" />
              <div className="skeleton-line w-1/3 h-2 sm:h-[7px] rounded-full" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Skeleton that mimics a chat conversation layout (user + assistant alternating) with input */
export function ChatSkeleton({ count = 5 }: { count?: number }) {
  const userMsgs = [
    { bubble: "w-[85%] sm:w-[75%]", lines: ["w-full", "w-[82%]"] },
    { bubble: "w-[70%] sm:w-[60%]", lines: ["w-full"] },
    { bubble: "w-[90%] sm:w-[80%]", lines: ["w-full", "w-[75%]"] },
    { bubble: "w-[75%] sm:w-[65%]", lines: ["w-full"] },
    { bubble: "w-[80%] sm:w-[70%]", lines: ["w-full", "w-[88%]"] },
  ];

  return (
    <div className="flex flex-col h-full animate-fade-in">
      {/* Message area */}
      <div className="flex-1 overflow-hidden space-y-3 sm:space-y-4">
        {Array.from({ length: count }).map((_, i) => {
          const msg = userMsgs[i % userMsgs.length];
          return (
            <div key={i}>
              <UserMessageSkeleton msg={msg} />
              <AssistantMessageSkeleton />
            </div>
          );
        })}
      </div>

      {/* ChatInput skeleton at bottom */}
      <ChatInputSkeleton />
    </div>
  );
}

/** Messages-only skeleton (for streaming footer, no input box) */
export function ChatSkeletonMessagesOnly({ count = 3 }: { count?: number }) {
  const userMsgs = [
    { bubble: "w-[85%] sm:w-[75%]", lines: ["w-full", "w-[82%]"] },
    { bubble: "w-[70%] sm:w-[60%]", lines: ["w-full"] },
    { bubble: "w-[90%] sm:w-[80%]", lines: ["w-full", "w-[75%]"] },
    { bubble: "w-[75%] sm:w-[65%]", lines: ["w-full"] },
    { bubble: "w-[80%] sm:w-[70%]", lines: ["w-full", "w-[88%]"] },
  ];

  return (
    <div className="animate-fade-in space-y-3 sm:space-y-4">
      {Array.from({ length: count }).map((_, i) => {
        const msg = userMsgs[i % userMsgs.length];
        return (
          <div key={i}>
            <UserMessageSkeleton msg={msg} />
            <AssistantMessageSkeleton />
          </div>
        );
      })}
    </div>
  );
}

/** Shared ChatInput skeleton — matches the compact chat composer container. */
function ChatInputSkeleton() {
  return (
    <div className="shrink-0 sm:px-4 pb-3 pt-1">
      <div className="mx-auto max-w-3xl lg:max-w-4xl xl:max-w-5xl px-2">
        <div
          className="flex flex-col w-full rounded-2xl px-1 border"
          style={{
            backgroundColor: "var(--theme-bg-card)",
            borderColor: "var(--theme-border)",
            boxShadow: "0 1px 6px rgba(15,23,42,0.07)",
          }}
        >
          {/* Textarea area */}
          <div className="px-2.5 pt-1 flex items-start gap-2">
            <div className="skeleton-line h-3 w-3/5 rounded flex-1 pt-[10px] min-h-[40px] sm:min-h-[44px]" />
          </div>
          {/* Toolbar — matches real toolbar: gap-1 sm:gap-2 */}
          <div className="flex justify-between flex-nowrap pt-3 pb-3 px-2 mx-0.5 max-w-full">
            <div className="flex items-center gap-1 sm:gap-2 self-end flex-1 min-w-0">
              <div className="skeleton-line h-8 w-8 rounded-lg shrink-0" />
              <div className="skeleton-line h-8 w-8 rounded-lg shrink-0 hidden sm:block" />
            </div>
            <div className="self-end flex shrink-0">
              <div className="skeleton-line size-8 rounded-full" />
            </div>
          </div>
        </div>
        {/* Keyboard shortcut hint — desktop only */}
        <div className="hidden sm:flex mx-auto mt-3 px-2 max-w-3xl lg:max-w-4xl xl:max-w-5xl justify-center">
          <SkeletonLine width="w-40" className="!h-3" />
        </div>
      </div>
    </div>
  );
}

/** Skeleton for the welcome page (greeting + input + suggestions) */
export function WelcomeSkeleton() {
  return (
    <div className="welcome-root relative flex h-full flex-col items-center justify-center px-4 overflow-hidden animate-fade-in">
      <div className="welcome-hero relative flex flex-col items-center mb-4 sm:mb-5 md:mb-6 w-full max-w-[90vw]">
        <div className="max-w-[90vw] w-full flex items-center justify-center">
          <SkeletonLine
            width="w-44 sm:w-60 md:w-64"
            className="!h-[1.5rem] sm:!h-[1.85rem] md:!h-8 !rounded-lg"
          />
        </div>
        <SkeletonLine
          width="w-36 sm:w-44 md:w-48"
          className="!h-3.5 sm:!h-4 mt-2 !rounded-lg"
        />
      </div>

      <div className="welcome-input w-full sm:max-w-[44rem] md:max-w-[46rem] lg:max-w-[48rem]">
        <div
          className="flex flex-col w-full rounded-2xl px-1 border"
          style={{
            backgroundColor: "var(--theme-bg-card)",
            borderColor: "var(--theme-border)",
            boxShadow: "0 1px 6px rgba(15,23,42,0.07)",
          }}
        >
          {/* Textarea area */}
          <div className="px-2.5 py-2 flex items-start gap-2">
            <div className="skeleton-line h-3 w-3/5 rounded flex-1 mt-3 min-h-[30px]" />
          </div>
          {/* Toolbar */}
          <div className="flex justify-between flex-nowrap pt-3 pb-3 px-2 mx-0.5 max-w-full">
            <div className="flex items-center gap-1 sm:gap-2 self-end flex-1 min-w-0">
              <div className="skeleton-line h-8 w-8 rounded-lg shrink-0" />
              <div className="skeleton-line h-8 w-8 rounded-lg shrink-0 hidden sm:block" />
            </div>
            <div className="self-end flex shrink-0">
              <div className="skeleton-line size-8 rounded-full" />
            </div>
          </div>
        </div>
      </div>

      {/* Suggestions skeleton */}
      <div className="welcome-suggestions relative w-[85%] sm:max-w-[38rem] md:max-w-[40rem] lg:max-w-[42rem] xl:max-w-[44rem] 2xl:max-w-[46rem] px-0 sm:px-4 sm:mt-2 md:mt-3 xl:mt-4 2xl:mt-4">
        {/* Label + refresh */}
        <div className="welcome-suggestions-header flex items-center justify-between mb-2 sm:mb-3 md:mb-3 xl:mb-4 2xl:mb-4 px-2 sm:px-0">
          <div className="flex items-center gap-1">
            <div className="skeleton-line size-[11px] sm:size-3.5 xl:size-4 rounded-full opacity-60" />
            <SkeletonLine
              width="w-20 sm:w-24"
              className="!h-3 sm:!h-3.5 xl:!h-4"
            />
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg">
              <div className="skeleton-line size-3 xl:size-3.5 rounded-sm" />
              <SkeletonLine
                width="w-14 sm:w-16"
                className="!h-[11px] sm:!h-3"
              />
            </div>
          </div>
        </div>
        {/* Suggestion grid — items >= 2 hidden on mobile */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-2.5 md:gap-2.5 xl:gap-3 2xl:gap-3 px-2 sm:px-0">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className={`welcome-card welcome-suggestion-pill group relative flex items-center gap-2 sm:gap-3 md:gap-3 xl:gap-3.5 2xl:gap-3.5 rounded-xl border px-3 py-2 sm:px-4 sm:py-3${
                i >= 2 ? " hidden sm:flex" : ""
              }`}
              style={{
                backgroundColor: "var(--theme-bg-card)",
                borderColor: "var(--theme-border)",
              }}
            >
              <div className="skeleton-line size-6 sm:size-7 xl:size-8 2xl:size-8 rounded-lg shrink-0" />
              <SkeletonLine
                width={i % 2 === 0 ? "w-3/4" : "w-4/5"}
                className="!h-[12.5px] sm:!h-[13.5px] flex-1"
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
