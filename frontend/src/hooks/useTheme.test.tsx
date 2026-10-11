import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test } from "vitest";

import { installMatchMedia, type FakeMatchMedia } from "../test/matchMedia";
import { THEME_STORAGE_KEY } from "../lib/theme";
import { useTheme } from "./useTheme";

let media: FakeMatchMedia;

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  media = installMatchMedia(true);
});

afterEach(() => {
  media.restore();
});

test("starts from the system preference and applies it", () => {
  media.set(false);

  const { result } = renderHook(() => useTheme());

  expect(result.current.theme).toBe("light");
  expect(document.documentElement.dataset.theme).toBe("light");
});

test("toggling flips the theme, applies it and remembers it", () => {
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("dark");

  act(() => result.current.toggle());

  expect(result.current.theme).toBe("light");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
});

test("follows the system while the user has made no choice", () => {
  const { result } = renderHook(() => useTheme());

  act(() => media.set(false));

  expect(result.current.theme).toBe("light");
});

test("stops following the system once the user chose", () => {
  const { result } = renderHook(() => useTheme());

  act(() => result.current.toggle()); // user picks light explicitly
  act(() => media.set(true)); // system flips to dark

  expect(result.current.theme).toBe("light");
  expect(document.documentElement.dataset.theme).toBe("light");
});

test("a stored choice is honoured on mount", () => {
  localStorage.setItem(THEME_STORAGE_KEY, "light");
  media.set(true);

  const { result } = renderHook(() => useTheme());

  expect(result.current.theme).toBe("light");
});

test("setTheme applies an explicit theme", () => {
  const { result } = renderHook(() => useTheme());

  act(() => result.current.setTheme("light"));

  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
});
