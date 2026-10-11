import { useCallback, useEffect, useState } from "react";

import {
  applyTheme,
  readStoredTheme,
  resolveInitialTheme,
  storeTheme,
  type Theme,
} from "../lib/theme";

/**
 * The active theme, with the rule that a deliberate choice stops the app from
 * following the OS — until the user picks again.
 */
export function useTheme(): {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
} {
  const [theme, setThemeState] = useState<Theme>(() => resolveInitialTheme());

  // Apply on mount and on every change.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Follow the OS only while the user has made no explicit choice.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = (event: MediaQueryListEvent) => {
      if (readStoredTheme() === null) {
        setThemeState(event.matches ? "light" : "dark");
      }
    };
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    storeTheme(next);
    setThemeState(next);
  }, []);

  const toggle = useCallback(() => {
    setThemeState((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      storeTheme(next);
      return next;
    });
  }, []);

  return { theme, setTheme, toggle };
}
