/**
 * Test helpers for anything that renders the app: the API provider, the
 * session provider, a router, and MSW handlers for `/v1/auth/*`.
 *
 * Keeping this in one place means a page test never has to remember that the
 * shell boots by asking the server who the caller is.
 */
import { render } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import { SessionProvider } from "../auth/context";
import App from "../App";

export const BASE = "http://127.0.0.1:8200";

export const SESSION_AUTHENTICATED = {
  authenticated: true,
  username: "stt",
  tenant: "local",
  ui_auth_enabled: true,
  expires_at: 4_102_444_800,
};

export const SESSION_AUTH_OFF = {
  authenticated: false,
  username: null,
  tenant: "local",
  ui_auth_enabled: false,
  expires_at: null,
};

/** `/v1/auth/me`: the session payload, or the 401 that means "sign in". */
export function meHandler(body: object = SESSION_AUTHENTICATED) {
  return http.get(`${BASE}/v1/auth/me`, () => HttpResponse.json(body));
}

/** `/v1/auth/config`: what the login page asks before anyone has a session. */
export function authConfigHandler(devCredentials = false, uiAuthEnabled = true) {
  return http.get(`${BASE}/v1/auth/config`, () =>
    HttpResponse.json({
      ui_auth_enabled: uiAuthEnabled,
      dev_credentials: devCredentials,
    }),
  );
}

export function meUnauthorized() {
  return http.get(`${BASE}/v1/auth/me`, () =>
    HttpResponse.json(
      { error: { code: "UNAUTHORIZED", message: "sign in to continue" } },
      { status: 401 },
    ),
  );
}

export function loginHandler(body?: object, status = 200) {
  return http.post(`${BASE}/v1/auth/login`, () =>
    status === 200
      ? HttpResponse.json(
          body ?? {
            username: "stt",
            tenant: "local",
            expires_at: 4_102_444_800,
            token: "v1.stub.stub",
          },
        )
      : HttpResponse.json(
          {
            error: {
              code: "UNAUTHORIZED",
              message: "invalid username or password",
            },
          },
          { status },
        ),
  );
}

export function logoutHandler() {
  return http.post(`${BASE}/v1/auth/logout`, () =>
    HttpResponse.json({ ok: true }),
  );
}

/** The app, wrapped in every provider it needs, at `path`. */
export function renderApp(path = "/", children?: ReactNode) {
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <SessionProvider>
        <MemoryRouter initialEntries={[path]}>
          {children ?? <App />}
        </MemoryRouter>
      </SessionProvider>
    </ApiProvider>,
  );
}

/** Just the session context and a router — for testing a single component. */
export function renderWithSession(
  ui: ReactNode,
  { path = "/" }: { path?: string } = {},
) {
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <SessionProvider>
        <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
      </SessionProvider>
    </ApiProvider>,
  );
}
