import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import { server } from "../test/server";
import {
  BASE,
  SESSION_AUTH_OFF,
  loginHandler,
  logoutHandler,
  meHandler,
  meUnauthorized,
} from "../test/app";
import { SessionProvider, useSession } from "./context";

const client = createApiClient(BASE);

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApiProvider client={client}>
      <SessionProvider>{children}</SessionProvider>
    </ApiProvider>
  );
}

test("boots by asking the server who the caller is", async () => {
  server.use(meHandler());

  const { result } = renderHook(() => useSession(), { wrapper });

  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.authenticated).toBe(true);
  expect(result.current.session?.username).toBe("stt");
  expect(result.current.canEnterApp).toBe(true);
});

test("a 401 on boot means a login is required", async () => {
  server.use(meUnauthorized());

  const { result } = renderHook(() => useSession(), { wrapper });

  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.authenticated).toBe(false);
  expect(result.current.loginRequired).toBe(true);
  expect(result.current.canEnterApp).toBe(false);
});

test("a server that needs no login lets everyone straight in", async () => {
  server.use(meHandler(SESSION_AUTH_OFF));

  const { result } = renderHook(() => useSession(), { wrapper });

  await waitFor(() => expect(result.current.status).toBe("ready"));
  expect(result.current.loginRequired).toBe(false);
  expect(result.current.canEnterApp).toBe(true);
});

test("a successful login adopts the session it is handed", async () => {
  server.use(meUnauthorized(), loginHandler());

  const { result } = renderHook(() => useSession(), { wrapper });
  await waitFor(() => expect(result.current.status).toBe("ready"));

  let ok = false;
  await act(async () => {
    ok = await result.current.login("stt", "stt");
  });

  expect(ok).toBe(true);
  await waitFor(() => expect(result.current.authenticated).toBe(true));
  expect(result.current.session?.username).toBe("stt");
  expect(result.current.loginError).toBeNull();
});

test("a failed login surfaces the server's message and stays anonymous", async () => {
  server.use(meUnauthorized(), loginHandler(undefined, 401));

  const { result } = renderHook(() => useSession(), { wrapper });
  await waitFor(() => expect(result.current.status).toBe("ready"));

  let ok = true;
  await act(async () => {
    ok = await result.current.login("stt", "wrong");
  });

  expect(ok).toBe(false);
  expect(result.current.authenticated).toBe(false);
  expect(result.current.loginError).toMatch(/invalid username or password/i);
});

test("logging out drops the session and returns to needing a login", async () => {
  server.use(meHandler(), logoutHandler());

  const { result } = renderHook(() => useSession(), { wrapper });
  await waitFor(() => expect(result.current.authenticated).toBe(true));

  await act(async () => {
    await result.current.logout();
  });

  await waitFor(() => expect(result.current.authenticated).toBe(false));
  expect(result.current.session).toBeNull();
  expect(result.current.canEnterApp).toBe(false);
});
