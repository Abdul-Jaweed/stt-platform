/**
 * MediaRecorder / getUserMedia stand-ins for tests.
 *
 * jsdom ships neither, and both need to be *controllable*: a test has to be
 * able to deny permission, hand back a stream it can inspect afterwards, and
 * push chunks at a moment of its choosing.
 */

export class FakeTrack {
  stopped = false;
  stop() {
    this.stopped = true;
  }
}

export class FakeStream {
  tracks = [new FakeTrack(), new FakeTrack()];
  getTracks() {
    return this.tracks;
  }
}

export class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  /** MIME types this "browser" admits to supporting. */
  static supported: string[] = ["audio/webm;codecs=opus", "audio/webm"];

  state: "inactive" | "recording" = "inactive";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  mimeType: string;

  constructor(
    public stream: FakeStream,
    options?: { mimeType?: string },
  ) {
    this.mimeType = options?.mimeType ?? "";
    FakeMediaRecorder.instances.push(this);
  }

  static isTypeSupported(type: string) {
    return FakeMediaRecorder.supported.includes(type);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.onstop?.();
  }

  emitChunk(text: string) {
    this.ondataavailable?.({
      data: new Blob([text], { type: this.mimeType }),
    });
  }

  fail() {
    this.onerror?.();
  }
}

export function installRecorderSupport(): void {
  FakeMediaRecorder.instances = [];
  (globalThis as { MediaRecorder?: unknown }).MediaRecorder = FakeMediaRecorder;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: async () => new FakeStream() },
  });
}

export function removeRecorderSupport(): void {
  delete (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: undefined,
  });
}
