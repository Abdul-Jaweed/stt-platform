import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { installMatchMedia, type FakeMatchMedia } from "../test/matchMedia";
import {
  applyTheme,
  readStoredTheme,
  resolveInitialTheme,
  storeTheme,
  systemTheme,
  THEME_STORAGE_KEY,
} from "./theme";

let media: FakeMatchMedia;

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  media = installMatchMedia(true);
});

afterEach(() => {
  media.restore();
});

describe("theme", () => {
  test("systemTheme reads prefers-color-scheme", () => {
    expect(systemTheme()).toBe("dark");
    media.set(false);
    expect(systemTheme()).toBe("light");
  });

  test("systemTheme is dark when matchMedia is unavailable", () => {
    media.restore();
    const original = window.matchMedia;
    // @ts-expect-error - simulating an ancient environment on purpose
    delete window.matchMedia;
    expect(systemTheme()).toBe("dark");
    window.matchMedia = original;
  });

  test("readStoredTheme ignores junk", () => {
    expect(readStoredTheme()).toBeNull();
    localStorage.setItem(THEME_STORAGE_KEY, "chartreuse");
    expect(readStoredTheme()).toBeNull();
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(readStoredTheme()).toBe("light");
  });

  test("a stored choice wins over the system", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(resolveInitialTheme()).toBe("light");
  });

  test("with no choice stored the system decides", () => {
    media.set(false);
    expect(resolveInitialTheme()).toBe("light");
  });

  test("applyTheme sets the attribute and the native color-scheme", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");

    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  test("storeTheme persists the choice", () => {
    storeTheme("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});
