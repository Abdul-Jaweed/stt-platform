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

// The streaming socket is a different animal from the REST proxy: nginx must
// forward the Upgrade/Connection handshake instead of answering it, and a
// session may be quiet between utterances, so its read timeout is the
// stream's own cap rather than the 300s an upload gets.
test("the streaming socket is proxied with the upgrade handshake", () => {
  const match = conf.match(/location\s*=?\s*([^\s{]*transcriptions\/stream[^\s{]*)\s*\{([^}]*)\}/);
  expect(match, "no location block for /v1/transcriptions/stream").not.toBeNull();
  const [, path, body] = match!;
  expect(path).toContain("/v1/transcriptions/stream");
  expect(body).toMatch(/proxy_http_version\s+1\.1/);
  expect(body).toMatch(/Upgrade\s+\$http_upgrade/);
  expect(body).toMatch(/Connection\s+"?upgrade"?/i);
  const timeout = body.match(/proxy_read_timeout\s+(\d+)s/);
  expect(timeout, "no proxy_read_timeout in the stream block").not.toBeNull();
  expect(Number(timeout![1])).toBeGreaterThanOrEqual(300);
  // ...and it keeps the re-resolution rule that fixed the login 502.
  expect(body).toMatch(/proxy_pass\s+\$api_stream/);
});
