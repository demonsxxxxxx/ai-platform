import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
import {
  applyThemeToDocument,
  getInitialThemePreference,
  getStoredThemePreference,
  THEME_STORAGE_KEY,
  type Theme,
} from "../utils/themeDom";

interface ThemeContextType {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

interface ThemeProviderProps {
  children: ReactNode;
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [theme, setThemeState] = useState<Theme>(getInitialThemePreference);
  const themeRef = useRef(theme);
  const hasExplicitPreference = useRef(getStoredThemePreference() !== null);

  useLayoutEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  // An OS-derived initial color is not a user choice. Keep following the OS
  // until the user explicitly chooses a theme, without writing account metadata.
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (e: MediaQueryListEvent) => {
      if (!hasExplicitPreference.current) {
        const next = e.matches ? "dark" : "light";
        themeRef.current = next;
        setThemeState(next);
      }
    };
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  const setTheme = (newTheme: Theme) => {
    hasExplicitPreference.current = true;
    themeRef.current = newTheme;
    setThemeState(newTheme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, newTheme);
    } catch {
      // A blocked browser store must not prevent this tab's explicit choice.
    }
  };

  const toggleTheme = () => {
    setTheme(themeRef.current === "light" ? "dark" : "light");
  };

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeContextType {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}
