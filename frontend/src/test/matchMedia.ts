/**
 * Controllable `matchMedia` for tests.
 *
 * jsdom has no `matchMedia`; the theme code needs one it can flip so
 * "follow the system until the user chooses" is actually testable.
 */
import { vi } from "vitest";

export interface FakeMatchMedia {
  matches: boolean;
  set(matches: boolean): void;
  listenerCount(): number;
  restore(): void;
}

export function installMatchMedia(initialDark = true): FakeMatchMedia {
  const state = {
    dark: initialDark,
    // Each listener is registered for a specific query, so the event it
    // receives must carry that query's answer — not the raw dark flag.
    listeners: new Map<(e: MediaQueryListEvent) => void, boolean>(),
  };
  const original = window.matchMedia;

  window.matchMedia = vi.fn((query: string) => {
    const wantsDark = query.includes("dark");
    return {
      media: query,
      get matches() {
        return wantsDark ? state.dark : !state.dark;
      },
      onchange: null,
      addEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) => {
        state.listeners.set(cb, wantsDark);
      },
      removeEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) => {
        state.listeners.delete(cb);
      },
      addListener: (cb: (e: MediaQueryListEvent) => void) =>
        state.listeners.set(cb, wantsDark),
      removeListener: (cb: (e: MediaQueryListEvent) => void) =>
        state.listeners.delete(cb),
      dispatchEvent: () => true,
    } as unknown as MediaQueryList;
  }) as typeof window.matchMedia;

  return {
    get matches() {
      return state.dark;
    },
    set(next: boolean) {
      state.dark = next;
      for (const [cb, wantsDark] of state.listeners) {
        cb({ matches: wantsDark ? next : !next } as MediaQueryListEvent);
      }
    },
    listenerCount: () => state.listeners.size,
    restore() {
      window.matchMedia = original;
    },
  };
}
