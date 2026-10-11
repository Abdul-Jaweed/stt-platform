import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";

import { authConfigHandler } from "./app";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// Every test gets a sane `/v1/auth/config` (login not required, no dev
// defaults): the login page asks for it on mount, and an unhandled request
// would otherwise read as "the server said no".
beforeEach(() => server.use(authConfigHandler(false)));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// jsdom has no object-URL store. Real browsers do, and the recorder hands the
// preview player a URL — so give the tests the same surface rather than
// weakening the app for the test environment.
if (typeof URL.createObjectURL !== "function") {
  let counter = 0;
  URL.createObjectURL = vi.fn(() => `blob:jsdom/${++counter}`);
  URL.revokeObjectURL = vi.fn();
}
