import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, vi } from "vitest";

import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
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
