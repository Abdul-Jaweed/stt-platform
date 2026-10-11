import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "./test/server";
import {
  BASE,
  SESSION_AUTHENTICATED,
  loginHandler,
  logoutHandler,
  meHandler,
  meUnauthorized,
  renderApp,
} from "./test/app";

function useAppHandlers() {
  server.use(
    meHandler(),
    loginHandler(),
    logoutHandler(),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
  );
}

test("the landing page is public: no session, no redirect", async () => {
  server.use(meUnauthorized());

  renderApp("/");

  expect(
    await screen.findByRole("heading", {
      level: 1,
      name: /measure what your models hear/i,
    }),
  ).toBeInTheDocument();
});

test("the app shell is protected: an anonymous visitor gets the login page", async () => {
  server.use(meUnauthorized());

  renderApp("/upload");

  expect(
    await screen.findByRole("heading", { name: /sign in/i }),
  ).toBeInTheDocument();
});

test("a signed-in visitor gets the app shell with its navigation", async () => {
  useAppHandlers();

  renderApp("/upload");

  expect(
    await screen.findByRole("heading", { name: /stt platform/i }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /providers/i })).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /evaluations/i }),
  ).toBeInTheDocument();
});

test("unknown routes are public and say so", async () => {
  server.use(meUnauthorized());

  renderApp("/nope");

  expect(await screen.findByText(/not found/i)).toBeInTheDocument();
});

test("signing out returns to the public page", async () => {
  useAppHandlers();

  renderApp("/upload");
  await screen.findByRole("heading", { name: /stt platform/i });

  const { default: userEvent } = await import("@testing-library/user-event");
  await userEvent.click(screen.getByRole("button", { name: /sign out/i }));

  await waitFor(() =>
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: /measure what your models hear/i,
      }),
    ).toBeInTheDocument(),
  );
});

test("the app shell shows who is signed in", async () => {
  useAppHandlers();

  renderApp("/upload");

  const chip = await screen.findByTestId("session-user");
  expect(chip).toHaveTextContent(SESSION_AUTHENTICATED.username);
});
