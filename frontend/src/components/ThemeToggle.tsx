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
      <svg
        viewBox="0 0 24 24"
        width="18"
        height="18"
        aria-hidden="true"
        focusable="false"
      >
        {isLight ? (
          <>
            {/* lit bulb: filament, glass, base, rays */}
            <path
              d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.8 1 .9 1.6l.1.6h5.2l.1-.6c.1-.6.4-1.2.9-1.6A6 6 0 0 0 12 3Z"
              fill="currentColor"
              opacity="0.9"
            />
            <path
              d="M9.6 18h4.8M10.4 20.5h3.2"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              fill="none"
            />
            <path
              d="M12 1.4v1.2M4.6 4.2l.9.9M19.4 4.2l-.9.9M2.6 11h1.2M20.2 11h1.2"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              fill="none"
            />
          </>
        ) : (
          <>
            {/* outline bulb */}
            <path
              d="M12 3.2a5.8 5.8 0 0 0-3.5 10.4c.5.4.9 1 1 1.7l.1.5h4.8l.1-.5c.1-.7.5-1.3 1-1.7A5.8 5.8 0 0 0 12 3.2Z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinejoin="round"
              fill="none"
            />
            <path
              d="M9.8 18h4.4M10.6 20.4h2.8"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              fill="none"
            />
          </>
        )}
      </svg>
    </button>
  );
}
