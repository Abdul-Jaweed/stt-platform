import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test } from "vitest";

import { installMatchMedia, type FakeMatchMedia } from "../test/matchMedia";
import ThemeToggle from "./ThemeToggle";

let media: FakeMatchMedia;

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  media = installMatchMedia(true);
});

afterEach(() => {
  media.restore();
});

test("offers to switch to the other theme", async () => {
  render(<ThemeToggle />);

  const button = await screen.findByRole("button", {
    name: /switch to light theme/i,
  });
  expect(button).toHaveAttribute("aria-pressed", "false");
  expect(button.querySelector("svg")).not.toBeNull();
});

test("clicking the bulb switches the theme and says so", async () => {
  render(<ThemeToggle />);

  await userEvent.click(
    screen.getByRole("button", { name: /switch to light theme/i }),
  );

  expect(document.documentElement.dataset.theme).toBe("light");
  expect(
    screen.getByRole("button", { name: /switch to dark theme/i }),
  ).toHaveAttribute("aria-pressed", "true");
});

test("the toggle reflects a theme chosen elsewhere", async () => {
  localStorage.setItem("stt.theme", "light");

  render(<ThemeToggle />);

  expect(
    await screen.findByRole("button", { name: /switch to dark theme/i }),
  ).toBeInTheDocument();
});
