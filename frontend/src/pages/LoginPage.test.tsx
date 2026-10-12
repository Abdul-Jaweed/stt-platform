import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test, vi } from "vitest";

import { server } from "../test/server";
import {
  BASE,
  authConfigHandler,
  loginHandler,
  meUnauthorized,
  renderApp,
} from "../test/app";
import LoginPage from "./LoginPage";

function AppWithLogin() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/upload" element={<h2>app home</h2>} />
      <Route path="/evaluations" element={<h2>evaluations page</h2>} />
    </Routes>
  );
}

test("renders a labelled form with a masked password", async () => {
  server.use(meUnauthorized());

  renderApp("/login", <AppWithLogin />);

  expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
  const password = screen.getByLabelText(/^password$/i);
  expect(password).toHaveAttribute("type", "password");
  expect(password).toHaveAttribute("autocomplete", "current-password");
});

test("asks for both fields before troubling the server", async () => {
  const posts = vi.fn();
  server.use(
    meUnauthorized(),
    loginHandler(),
  );
  server.events.on("request:start", ({ request }) => {
    if (request.url.includes("/v1/auth/login")) posts();
  });

  renderApp("/login", <AppWithLogin />);
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText(/enter your username/i)).toBeInTheDocument();
  expect(screen.getByText(/enter your password/i)).toBeInTheDocument();
  expect(posts).not.toHaveBeenCalled();
});

test("shows the server's message when the credentials are wrong", async () => {
  server.use(meUnauthorized(), loginHandler(undefined, 401));

  renderApp("/login", <AppWithLogin />);
  await userEvent.type(screen.getByLabelText(/username/i), "stt");
  await userEvent.type(screen.getByLabelText(/^password$/i), "nope");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/invalid username or password/i);
  expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
});

test("signs in and lands on the app home", async () => {
  server.use(meUnauthorized(), loginHandler());

  renderApp("/login", <AppWithLogin />);
  await userEvent.type(screen.getByLabelText(/username/i), "stt");
  await userEvent.type(screen.getByLabelText(/^password$/i), "stt");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText("app home")).toBeInTheDocument();
});

test("honours an internal redirect target", async () => {
  server.use(meUnauthorized(), loginHandler());

  renderApp("/login?next=%2Fevaluations", <AppWithLogin />);
  await userEvent.type(screen.getByLabelText(/username/i), "stt");
  await userEvent.type(screen.getByLabelText(/^password$/i), "stt");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText("evaluations page")).toBeInTheDocument();
});

test("refuses to be used as an open redirect", async () => {
  server.use(meUnauthorized(), loginHandler());

  renderApp("/login?next=https%3A%2F%2Fevil.example%2Fsteal", <AppWithLogin />);
  await userEvent.type(screen.getByLabelText(/username/i), "stt");
  await userEvent.type(screen.getByLabelText(/^password$/i), "stt");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText("app home")).toBeInTheDocument();
});

test("toggles password visibility with an accessible name", async () => {
  server.use(meUnauthorized());

  renderApp("/login", <AppWithLogin />);
  const password = screen.getByLabelText(/^password$/i);

  await userEvent.click(screen.getByRole("button", { name: /show password/i }));
  expect(password).toHaveAttribute("type", "text");

  await userEvent.click(screen.getByRole("button", { name: /hide password/i }));
  expect(password).toHaveAttribute("type", "password");
});

test("blocks a second submission while the first is in flight", async () => {
  server.use(
    meUnauthorized(),
    http.post(`${BASE}/v1/auth/login`, async () => {
      await delay(120);
      return HttpResponse.json({
        username: "stt",
        tenant: "local",
        expires_at: 4_102_444_800,
        token: "v1.stub.stub",
      });
    }),
  );

  renderApp("/login", <AppWithLogin />);
  await userEvent.type(screen.getByLabelText(/username/i), "stt");
  await userEvent.type(screen.getByLabelText(/^password$/i), "stt");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  const submit = screen.getByRole("button", { name: /signing in/i });
  expect(submit).toBeDisabled();
  await waitFor(() => expect(screen.getByText("app home")).toBeInTheDocument());
});

test("says nothing about default credentials when the deployment uses real ones", async () => {
  server.use(meUnauthorized(), authConfigHandler(false));

  renderApp("/login", <AppWithLogin />);

  await screen.findByRole("heading", { name: /sign in/i });
  await waitFor(() =>
    expect(screen.queryByText(/development default credentials/i)).toBeNull(),
  );
});

test("admits the development defaults only while they are actually in force", async () => {
  // The old hint text is gone: the panel carries the admission now, and it
  // may only appear when the server says demo mode is on *and* it published
  // credentials that actually work.
  server.use(
    meUnauthorized(),
    authConfigHandler(true, true, {
      demo_mode: true,
      demo_username: "stt",
      demo_password: "stt",
    }),
  );

  renderApp("/login", <AppWithLogin />);

  expect(
    await screen.findByText(/shipped development password/i),
  ).toBeInTheDocument();
});

test("one click fills the published demo credentials", async () => {
  server.use(
    meUnauthorized(),
    authConfigHandler(true, true, {
      demo_mode: true,
      demo_username: "stt",
      demo_password: "stt",
    }),
  );

  renderApp("/login", <AppWithLogin />);
  await userEvent.click(
    await screen.findByRole("button", { name: /use demo credentials/i }),
  );

  expect(screen.getByLabelText(/username/i)).toHaveValue("stt");
  expect(screen.getByLabelText(/^password$/i)).toHaveValue("stt");
});

test("a generated password is never filled — the panel says where it lives", async () => {
  server.use(
    meUnauthorized(),
    authConfigHandler(false, true, {
      demo_mode: true,
      demo_username: "stt",
      demo_password: null,
    }),
  );

  renderApp("/login", <AppWithLogin />);
  await userEvent.click(
    await screen.findByRole("button", { name: /use demo credentials/i }),
  );

  expect(screen.getByLabelText(/username/i)).toHaveValue("stt");
  expect(screen.getByLabelText(/^password$/i)).toHaveValue("");
  expect(screen.getByText(/STT_UI_PASSWORD/)).toBeInTheDocument();
});

test("no demo panel when demo mode is off", async () => {
  server.use(meUnauthorized(), authConfigHandler(false));

  renderApp("/login", <AppWithLogin />);

  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: /use demo credentials/i }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.queryByText(/development default credentials/i)).not.toBeInTheDocument();
});
