import { Component, ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import i18n from "i18next";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("[ErrorBoundary] Caught error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      const t = i18n.t.bind(i18n);
      return (
        <div className="flex min-h-screen items-center justify-center bg-[var(--theme-bg)] px-4 dark:bg-stone-950">
          <div className="w-full max-w-[380px] rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-8 text-center shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:bg-stone-900 sm:max-w-[420px] sm:p-10">
            <div className="mx-auto mb-5 w-14 h-14 rounded-full bg-amber-50 dark:bg-amber-500/10 flex items-center justify-center">
              <AlertTriangle className="w-7 h-7 text-amber-500 dark:text-amber-400" />
            </div>
            <h1 className="mb-2 text-xl font-semibold text-[var(--theme-text)]">
              {t("errorBoundary.title")}
            </h1>
            <p className="mb-6 break-words text-sm leading-relaxed text-[var(--theme-text-secondary)]">
              {this.state.error?.message || t("errorBoundary.unexpectedError")}
            </p>
            <button
              onClick={() => window.location.reload()}
              className="btn-primary inline-flex w-full items-center justify-center gap-2 px-4 py-2.5"
            >
              <RotateCcw className="w-4 h-4" />
              {t("errorBoundary.reloadPage")}
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
