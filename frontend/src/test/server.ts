import { setupServer } from "msw/node";

/** MSW node server: handlers are registered per-test with server.use(). */
export const server = setupServer();
