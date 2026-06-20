import { Link } from "react-router-dom";
import { ThemeToggle } from "../common/ThemeToggle";
import { LanguageToggle } from "../common/LanguageToggle";
import { APP_NAME } from "../../constants";

interface AuthLayoutProps {
  children: React.ReactNode;
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="auth-shell min-h-[100svh] min-h-[100dvh] overflow-y-auto overflow-x-hidden">
      <div className="auth-crosshatch" aria-hidden="true" />
      <nav className="fixed top-0 inset-x-0 z-50 bg-white/90 dark:bg-stone-950/90 border-b border-stone-100/60 dark:border-stone-800/40 transition-shadow duration-300">
        <div className="mx-auto flex h-14 max-w-full items-center justify-between px-4 sm:px-8">
          <Link to="/" className="flex items-center gap-2.5 group">
            <img
              src="/icons/icon.svg"
              alt={APP_NAME}
              className="w-6 h-6 rounded-md"
            />
            <span className="text-[15px] font-semibold text-stone-900 dark:text-stone-100">
              {APP_NAME}
            </span>
          </Link>
          <div className="flex items-center gap-1.5">
            <LanguageToggle />
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
