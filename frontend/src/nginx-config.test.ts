import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

const conf = readFileSync(join(process.cwd(), "nginx.conf"), "utf8");

// nginx resolves a hostname in proxy_pass ONCE, when the configuration loads.
// The API container is recreated by any image or env change and comes back with
// a different address, while this container keeps running: a literal upstream
// then answers every /v1 request with 502 until nginx itself is restarted.
// Proxying through a variable plus a resolver makes nginx ask Docker's embedded
// DNS again (valid=10s), so a recreated API is picked up without touching the UI.
const proxied = [...conf.matchAll(/proxy_pass\s+([^;]+);/g)].map((m) => m[1].trim());

test("the proxy config actually proxies something", () => {
  expect(proxied.length).toBeGreaterThan(0);
});

test("every proxy_pass goes through a variable, not a baked-in address", () => {
  const literal = proxied.filter((target) => !target.includes("$"));
  expect(literal).toEqual([]);
});

test("the resolver is Docker's embedded DNS with a short validity", () => {
  const match = conf.match(/resolver\s+(\S+)[^;]*valid=(\d+)s/);
  expect(match, "no `resolver ... valid=Ns` in nginx.conf").not.toBeNull();
  expect(match![1]).toBe("127.0.0.11");
  expect(Number(match![2])).toBeLessThanOrEqual(30);
});
