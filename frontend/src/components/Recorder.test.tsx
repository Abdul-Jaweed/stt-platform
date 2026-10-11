/**
 * The recorder panel: start → tick → stop → hand the clip to the page.
 *
 * The component owns no submission logic — it produces a File and tells the
 * page, so the recorded clip travels the exact same upload path as a chosen
 * file (and gets the same retention, playback and error handling).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  FakeMediaRecorder,
  installRecorderSupport,
  removeRecorderSupport,
} from "../test/mediaRecorder";
import Recorder from "./Recorder";

beforeEach(() => {
  installRecorderSupport();
});

afterEach(() => {
  removeRecorderSupport();
});

describe("Recorder", () => {
  test("records and hands the page a file", async () => {
    const onRecorded = vi.fn();
    render(<Recorder onRecorded={onRecorded} />);

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );
    expect(
      await screen.findByRole("button", { name: /stop recording/i }),
    ).toBeInTheDocument();

    FakeMediaRecorder.instances[0].emitChunk("clip-bytes");
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));

    await waitFor(() => expect(onRecorded).toHaveBeenCalledTimes(1));
    const file = onRecorded.mock.calls[0][0] as File;
    expect(file).toBeInstanceOf(File);
    expect(file.name).toMatch(/^recording-.*\.webm$/);
  });

  test("shows a preview player and can discard the clip", async () => {
    const onRecorded = vi.fn();
    render(<Recorder onRecorded={onRecorded} />);

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /stop recording/i }),
    );

    expect(
      await screen.findByLabelText(/recording preview/i),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /discard/i }));

    expect(screen.queryByLabelText(/recording preview/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /start recording/i }),
    ).toBeInTheDocument();
  });

  test("says so plainly when the browser cannot record", () => {
    removeRecorderSupport();
    render(<Recorder onRecorded={vi.fn()} />);

    expect(screen.getByText(/cannot record audio/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /start recording/i }),
    ).not.toBeInTheDocument();
  });

  test("surfaces a denied microphone as an alert", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () =>
          Promise.reject(
            Object.assign(new Error("denied"), { name: "NotAllowedError" }),
          ),
      },
    });
    render(<Recorder onRecorded={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /microphone permission was denied/i,
    );
  });
});
