import { screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { renderApp } from "../test/app";
import LandingPage from "./LandingPage";

function renderLanding() {
  return renderApp("/", <LandingPage />);
}

test("leads with a headline, the value proposition and the calls to action", () => {
  renderLanding();

  expect(
    screen.getByRole("heading", { level: 1, name: /measure what your models hear/i }),
  ).toBeInTheDocument();
  expect(screen.getByText(/one api for every speech-to-text provider/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /get started/i })).toHaveAttribute(
    "href",
    "/login",
  );
  expect(screen.getByRole("link", { name: /^sign in$/i })).toHaveAttribute(
    "href",
    "/login",
  );
});

test("explains the platform in features, not adjectives", () => {
  renderLanding();

  for (const heading of [
    /provider-neutral/i,
    /timestamped/i,
    /live transcription/i,
    /long audio/i,
    /tenant isolation/i,
    /evaluation/i,
  ]) {
    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
  }
});

test("names the providers that actually ship", () => {
  renderLanding();

  // Exact strings: the feature copy mentions the same names in prose.
  expect(screen.getByText("Faster-Whisper")).toBeInTheDocument();
  expect(screen.getByText("Deepgram")).toBeInTheDocument();
  expect(screen.getByText("ElevenLabs")).toBeInTheDocument();
  expect(screen.getByText("Sarvam")).toBeInTheDocument();
});

test("quotes measured numbers and says where they come from", () => {
  renderLanding();

  expect(screen.getByText(/measured on the built-in evaluation corpus/i)).toBeInTheDocument();
});

test("carries the theme toggle so the public page matches the app", () => {
  renderLanding();

  expect(
    screen.getByRole("button", { name: /switch to (light|dark) theme/i }),
  ).toBeInTheDocument();
});

test("links back to the app's public pages without requiring a session", () => {
  renderLanding();

  expect(screen.getByRole("contentinfo")).toBeInTheDocument();
});

test("tells a newcomer how to run the whole thing in one command", () => {
  renderLanding();

  expect(
    screen.getByRole("heading", { name: /run it yourself/i }),
  ).toBeInTheDocument();
  // The literal command, because a marketing page that cannot be followed is
  // just decoration.
  expect(screen.getByText("./launch.sh")).toBeInTheDocument();
  expect(
    screen.getByText(/docker desktop or docker engine/i),
  ).toBeInTheDocument();
  expect(screen.getByText("http://127.0.0.1:8080")).toBeInTheDocument();
});
