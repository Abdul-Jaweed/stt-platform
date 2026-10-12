/**
 * A programmable stand-in for `WebSocket`.
 *
 * jsdom ships a WebSocket that dials a real socket, which makes a test's
 * timing the server's business. This one lets the test decide when the
 * server "answers", when it dies, and exactly what was sent.
 */
export class FakeWebSocket {
  static CONNECTING = 0 as const;
  static OPEN = 1 as const;
  static CLOSING = 2 as const;
  static CLOSED = 3 as const;
  static instances: FakeWebSocket[] = [];

  url: string;
  readyState: number = FakeWebSocket.CONNECTING;
  /** Everything the client sent, in order: JSON strings and binary frames. */
  sent: (string | ArrayBuffer)[] = [];
  closedWith: number | null = null;

  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  static reset(): void {
    FakeWebSocket.instances = [];
  }

  static get latest(): FakeWebSocket {
    const latest = FakeWebSocket.instances.at(-1);
    if (!latest) throw new Error("no FakeWebSocket was constructed");
    return latest;
  }

  send(data: string | ArrayBuffer): void {
    if (this.readyState !== FakeWebSocket.OPEN) {
      throw new Error(`send() while readyState=${this.readyState}`);
    }
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.closedWith = code;
    this.readyState = FakeWebSocket.CLOSED;
  }

  // ---------------------------------------------------------------- controls
  /** The handshake succeeded. */
  accept(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** The server said something. */
  deliver(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }

  /** The connection failed before it was usable. */
  fail(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onerror?.(new Event("error"));
    this.onclose?.({ code: 1006 } as CloseEvent);
  }

  /** The server ended the connection cleanly. */
  serverClose(code = 1000): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  /** The binary frames the client sent, as byte arrays. */
  binarySent(): Uint8Array[] {
    return this.sent
      .filter((item): item is ArrayBuffer => item instanceof ArrayBuffer)
      .map((buffer) => new Uint8Array(buffer));
  }

  /** The JSON control frames the client sent. */
  controlSent(): { type?: string; [key: string]: unknown }[] {
    return this.sent
      .filter((item): item is string => typeof item === "string")
      .map((item) => JSON.parse(item) as { type?: string });
  }
}
