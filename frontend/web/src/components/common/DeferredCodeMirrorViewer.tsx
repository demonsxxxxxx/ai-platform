import { Suspense, lazy } from "react";

import type { CodeMirrorViewerProps } from "./CodeMirrorViewer";

const LazyCodeMirrorViewer = lazy(() =>
  import("./CodeMirrorViewer").then((module) => ({
    default: module.CodeMirrorViewer,
  })),
);

function CodeMirrorFallback({
  value,
  className,
  maxHeight,
  fontSize,
}: Pick<
  CodeMirrorViewerProps,
  "value" | "className" | "maxHeight" | "fontSize"
>) {
  return (
    <div className={className}>
      <pre
        className="overflow-auto whitespace-pre-wrap break-words bg-[var(--theme-on-overlay)] p-3 font-mono text-neutral-700 dark:bg-[var(--theme-bg-card)] dark:text-neutral-200"
        style={{
          ...(maxHeight ? { maxHeight } : {}),
          ...(fontSize ? { fontSize } : {}),
        }}
      >
        {value}
      </pre>
    </div>
  );
}

export function DeferredCodeMirrorViewer(props: CodeMirrorViewerProps) {
  return (
    <Suspense
      fallback={
        <CodeMirrorFallback
          value={props.value}
          className={props.className}
          maxHeight={props.maxHeight}
          fontSize={props.fontSize}
        />
      }
    >
      <LazyCodeMirrorViewer {...props} />
    </Suspense>
  );
}
