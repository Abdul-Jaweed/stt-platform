/**
 * The recorder hook: permission handling, ticking timer, blob assembly, and
 * the promise that the microphone is never left hot.
 *
 * jsdom has neither MediaRecorder nor getUserMedia, so both are stubbed here —
 * which is also the honest way to test permission denial and unsupported
 * browsers, states a real device rarely produces on demand.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { pickMimeType, useRecorder } from "./useRecorder";

class FakeTrack {
  stopped = false;
  stop() {
    this.stopped = true;
  }
}

class FakeStream {
  tracks = [new FakeTrack(), new FakeTrack()];
  getTracks() {
    return this.tracks;
  }
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static supported = ["audio/webm;codecs=opus", "audio/webm"];
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
    this.ondataavailable?.({ data: new Blob([text], { type: this.mimeType }) });
  }
}

function installRecorderSupport() {
  (globalThis as { MediaRecorder?: unknown }).MediaRecorder = FakeMediaRecorder;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => new FakeStream()),
    },
  });
}

function removeRecorderSupport() {
  delete (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: undefined,
  });
}

beforeEach(() => {
  FakeMediaRecorder.instances = [];
  installRecorderSupport();
});

afterEach(() => {
  removeRecorderSupport();
  vi.useRealTimers();
});

describe("pickMimeType", () => {
  test("prefers opus/webm when the browser supports it", () => {
    expect(pickMimeType()).toBe("audio/webm;codecs=opus");
  });

  test("falls back to what the browser does support", () => {
    FakeMediaRecorder.supported = ["audio/mp4"];
    expect(pickMimeType()).toBe("audio/mp4");
  });

  test("returns null when nothing is supported (browser default wins)", () => {
    FakeMediaRecorder.supported = [];
    expect(pickMimeType()).toBeNull();
  });
});

describe("useRecorder", () => {
  test("reports itself unsupported without MediaRecorder", () => {
    removeRecorderSupport();
    const { result } = renderHook(() => useRecorder());
    expect(result.current.supported).toBe(false);
  });

  test("records, ticks, and assembles a named file", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRecorder());

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.status).toBe("recording");
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: true,
    });

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.seconds).toBe(3);

    const recorder = FakeMediaRecorder.instances[0];
    act(() => {
      recorder.emitChunk("hello");
      recorder.emitChunk(" world");
      result.current.stop();
    });

    expect(result.current.status).toBe("ready");
    const file = result.current.clip?.file;
    expect(file).toBeInstanceOf(File);
    expect(file?.name).toMatch(/^recording-.*\.webm$/);
    // jsdom's Blob keeps only the MIME essence (parameters stripped), a real
    // browser keeps `audio/webm;codecs=opus` — assert what both agree on.
    expect(file?.type).toMatch(/^audio\/webm/);
    // jsdom's Blob has no `.text()`; the project reads blobs with FileReader.
    // FileReader schedules real macrotasks, so leave fake timers first.
    vi.useRealTimers();
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(file as Blob);
    });
    expect(text).toBe("hello world");
    expect(result.current.clip?.seconds).toBe(3);
  });

  test("stops the microphone when the recording ends", async () => {
    const { result } = renderHook(() => useRecorder());
    await act(async () => {
      await result.current.start();
    });
    const stream = FakeMediaRecorder.instances[0].stream;

    act(() => {
      result.current.stop();
    });

    expect(stream.tracks.every((track) => track.stopped)).toBe(true);
  });

  test("reset discards the clip and releases the microphone", async () => {
    const { result } = renderHook(() => useRecorder());
    await act(async () => {
      await result.current.start();
    });
    const stream = FakeMediaRecorder.instances[0].stream;
    act(() => {
      FakeMediaRecorder.instances[0].emitChunk("x");
      result.current.stop();
    });
    expect(result.current.clip).not.toBeNull();

    act(() => {
      result.current.reset();
    });

    expect(result.current.status).toBe("idle");
    expect(result.current.clip).toBeNull();
    expect(result.current.seconds).toBe(0);
    expect(stream.tracks.every((track) => track.stopped)).toBe(true);
  });

  test("a denied permission is a readable error, not a crash", async () => {
    const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(async () => Promise.reject(denied)) },
    });
    const { result } = renderHook(() => useRecorder());

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatch(/microphone/i);
    expect(result.current.clip).toBeNull();
  });

  test("no microphone on the machine is also a readable error", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () =>
          Promise.reject(
            Object.assign(new Error("no device"), { name: "NotFoundError" }),
          ),
        ),
      },
    });
    const { result } = renderHook(() => useRecorder());

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatch(/no microphone|not found/i);
  });

  test("the level meter is best-effort and never throws without WebAudio", async () => {
    const { result } = renderHook(() => useRecorder());
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.level).toBe(0);
  });
});
