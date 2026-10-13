import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

/** Handlers given to `setupServer` are the *initial* set: `resetHandlers()`
 * restores them after every test, so a call a test does not care about still
 * gets a defined answer instead of tripping the "error" unhandled setting. */
const defaults = [
  // The dataset picker. An empty list keeps the free-text fallback — the
  // behaviour every test predating the picker exercises; tests that cover
  // the picker register their own handler with server.use().
  http.get("http://127.0.0.1:8200/v1/evaluations/datasets", () =>
    HttpResponse.json({ datasets: [] }),
  ),
];

export const server = setupServer(...defaults);
