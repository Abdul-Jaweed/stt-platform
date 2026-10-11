import { Lightbulb, LightbulbFilament } from "@phosphor-icons/react";

import { useTheme } from "../hooks/useTheme";

/**
 * A bulb that switches the theme.
 *
 * The icon carries the state: an outline bulb in dark mode (light is what you
 * would switch *to*), a lit bulb in light mode. The accessible name says the
 * action, `aria-pressed` says the state — colour is never the only signal.
 */
export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const isLight = theme === "light";
  const label = isLight ? "Switch to dark theme" : "Switch to light theme";

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label={label}
      aria-pressed={isLight}
      title={label}
    >
      {isLight ? (
        <LightbulbFilament size={19} weight="fill" aria-hidden="true" />
      ) : (
        <Lightbulb size={19} aria-hidden="true" />
      )}
    </button>
  );
}
