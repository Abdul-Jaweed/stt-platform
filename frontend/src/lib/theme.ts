/**
 * Theme resolution, kept in one place.
 *
 * Precedence: an explicit choice the user made (localStorage) beats the
 * operating system's preference, which beats the dark default. The same
 * helpers run in `main.tsx` and in the pre-paint script in `index.html`, so
 * the rule cannot drift between them.
 */

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "stt.theme";

export function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/** The OS preference, or dark when the browser cannot tell us. */
export function systemTheme(): Theme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "dark";
  }
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  } catch {
    return "dark";
  }
}

export function readStoredTheme(): Theme | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(raw) ? raw : null;
  } catch {
    return null;
  }
}

export function resolveInitialTheme(): Theme {
  return readStoredTheme() ?? systemTheme();
}

/** Apply a theme to the document: the CSS hook *and* native control colours. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // A blocked localStorage (private mode, hardened browser) must not break
    // theming for this session.
  }
}
