import { Link } from "react-router-dom";
import { ThemeToggle } from "../common/ThemeToggle";
import { APP_NAME } from "../../constants";

interface AuthLayoutProps {
  children: React.ReactNode;
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="auth-shell min-h-[100svh] min-h-[100dvh] overflow-y-auto overflow-x-hidden">
      <div className="auth-crosshatch" aria-hidden="true" />
      <div className="auth-atmosphere" aria-hidden="true">
        <div className="auth-glow-main absolute top-0 left-1/2 -translate-x-1/2 w-[700px] h-[500px] bg-[radial-gradient(ellipse_at_center,color-mix(in_srgb,var(--theme-text)_6%,transparent)_0%,color-mix(in_srgb,var(--theme-text)_2.5%,transparent)_40%,transparent_70%)] dark:bg-[radial-gradient(ellipse_at_center,color-mix(in_srgb,var(--theme-text)_3.5%,transparent)_0%,color-mix(in_srgb,var(--theme-text)_1.5%,transparent)_40%,transparent_70%)]" />
        <div className="auth-glow-blue absolute top-[30%] left-[5%] w-[350px] h-[350px] bg-[radial-gradient(circle,color-mix(in_srgb,var(--theme-text)_3.5%,transparent)_0%,transparent_60%)] dark:bg-[radial-gradient(circle,color-mix(in_srgb,var(--theme-text)_2.5%,transparent)_0%,transparent_60%)]" />
        <div className="auth-glow-violet absolute bottom-[15%] right-[10%] w-[280px] h-[280px] bg-[radial-gradient(circle,color-mix(in_srgb,var(--theme-text)_3%,transparent)_0%,transparent_60%)] dark:bg-[radial-gradient(circle,color-mix(in_srgb,var(--theme-text)_1.8%,transparent)_0%,transparent_60%)]" />
      </div>

      <nav className="fixed top-0 inset-x-0 z-50 bg-[color-mix(in_srgb,var(--theme-bg-card)_90%,transparent)] dark:bg-neutral-950/90 border-b border-neutral-100/60 dark:border-neutral-800/40 transition-shadow duration-300">
        <div className="mx-auto flex h-14 max-w-full items-center justify-between px-4 sm:px-8">
          <Link to="/" className="flex items-center gap-2.5 group">
            <img
              src="/icons/icon.svg"
              alt={APP_NAME}
              className="w-6 h-6 sm:w-7 sm:h-7 rounded-lg transition-transform duration-300 group-hover:scale-105"
            />
            <span className="text-[15px] sm:text-lg font-bold tracking-tight text-neutral-900 dark:text-neutral-100 font-serif">
              {APP_NAME}
            </span>
          </Link>
          <div className="flex items-center gap-1.5">
            <ThemeToggle />
          </div>
        </div>
      </nav>

      <div className="relative z-10 flex min-h-[100svh] min-h-[100dvh] items-center justify-center px-4 py-20 sm:px-6 sm:py-24">
        <div className="w-full max-w-[22.5rem] sm:max-w-[450px]">
          {children}
        </div>
      </div>
    </div>
  );
}
