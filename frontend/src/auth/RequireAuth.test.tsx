import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";

import { server } from "../test/server";
import {
  SESSION_AUTH_OFF,
  meHandler,
  meUnauthorized,
  renderWithSession,
} from "../test/app";
import LoginPage from "../pages/LoginPage";
import RequireAuth from "./RequireAuth";

function Protected() {
  return (
    <Routes>
      <Route
        path="/upload"
        element={
          <RequireAuth>
            <h2>protected content</h2>
          </RequireAuth>
        }
      />
      <Route path="/login" element={<LoginPage />} />
    </Routes>
  );
}

test("an anonymous visitor is sent to the login page, remembering where they were headed", async () => {
  server.use(meUnauthorized());

  renderWithSession(<Protected />, { path: "/upload" });

  await waitFor(() =>
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument(),
  );
});

test("the protected content appears once the session is known", async () => {
  server.use(meHandler());

  renderWithSession(<Protected />, { path: "/upload" });

  expect(await screen.findByText("protected content")).toBeInTheDocument();
});

test("no login is asked for when the server does not require one", async () => {
  server.use(meHandler(SESSION_AUTH_OFF));

  renderWithSession(<Protected />, { path: "/upload" });

  expect(await screen.findByText("protected content")).toBeInTheDocument();
});
