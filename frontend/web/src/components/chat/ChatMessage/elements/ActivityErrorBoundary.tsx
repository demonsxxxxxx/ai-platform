import { Component, type ErrorInfo, type ReactNode } from "react";

interface ActivityErrorBoundaryProps {
  children: ReactNode;
  /** Reset the local fallback when the owning public part is replaced. */
  resetKey?: unknown;
}

interface ActivityErrorBoundaryState {
  failed: boolean;
  resetKey?: unknown;
}

/** Keeps one malformed activity renderer from taking down the transcript. */
export class ActivityErrorBoundary extends Component<ActivityErrorBoundaryProps, ActivityErrorBoundaryState> {
  state: ActivityErrorBoundaryState = {
    failed: false,
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromProps(
    props: ActivityErrorBoundaryProps,
    state: ActivityErrorBoundaryState,
  ): Partial<ActivityErrorBoundaryState> | null {
    if (props.resetKey !== state.resetKey) {
      return { failed: false, resetKey: props.resetKey };
    }
    return null;
  }

  static getDerivedStateFromError(): Partial<ActivityErrorBoundaryState> {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // The public fallback deliberately does not retain or expose the error.
  }

  render() {
    if (this.state.failed) {
      return (
        <div data-slot="activity-error" role="status" className="my-1 text-xs text-[var(--theme-text-secondary)]">
          活动暂时无法显示。
        </div>
      );
    }
    return this.props.children;
  }
}
