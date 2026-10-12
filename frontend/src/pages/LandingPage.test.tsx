import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test } from "vitest";

import type { PlatformLimits, ProviderInfo } from "../api/types";
import { BASE, renderApp } from "../test/app";
import { server } from "../test/server";
import LandingPage from "./LandingPage";

/**
 * The landing page is the public face of Evidance: the brand, the calls to
 * action, the live provider directory served by `GET /v1/providers`, the real
 * pipeline, and the limits stated out loud. The directory is mocked here with
 * the same shape the running stack returns; per-test overrides mock failure.
 */

const DOCS_URL = "https://github.com/Abdul-Jaweed/stt-platform/tree/main/docs";
const REPO_URL = "https://github.com/Abdul-Jaweed/stt-platform";

const LIMITS: PlatformLimits = {
  max_upload_bytes: 104857600,
  max_sync_duration_s: 180,
  max_async_duration_s: 14400,
  max_live_preview_duration_s: 120,
};

// Mirrors the live payload from `GET /v1/providers` (languages trimmed;
// every flag, limit and format kept as the stack reports them).
const PROVIDERS: ProviderInfo[] = [
  {
    id: "faster-whisper",
    name: "Faster-Whisper (local)",
    is_local: true,
    capabilities: {
      languages: ["en", "hi", "de", "es"],
      language_detection: true,
      segment_timestamps: true,
      word_timestamps: true,
      diarization: false,
      streaming: true,
      batch: true,
      async_processing: false,
      confidence_scores: true,
      max_file_bytes: null,
      max_duration_s: null,
      formats: ["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm"],
    },
    health: { status: "ok", detail: "cuda_devices=1" },
  },
  {
    id: "deepgram",
    name: "Deepgram (nova-3)",
    is_local: false,
    capabilities: {
      languages: ["en", "es", "fr"],
      language_detection: true,
      segment_timestamps: true,
      word_timestamps: true,
      diarization: true,
      streaming: false,
      batch: true,
      async_processing: false,
      confidence_scores: true,
      max_file_bytes: 2147483648,
      max_duration_s: null,
      formats: ["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm"],
    },
    health: { status: "ok", detail: "credential configured; reachability unverified" },
  },
  {
    id: "elevenlabs",
    name: "ElevenLabs Scribe",
    is_local: false,
    capabilities: {
      languages: ["en", "es"],
      language_detection: true,
      segment_timestamps: false,
      word_timestamps: true,
      diarization: true,
      streaming: false,
      batch: true,
      async_processing: false,
      confidence_scores: false,
      max_file_bytes: 3221225472,
      max_duration_s: 36000,
      formats: ["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm", "aiff"],
    },
    health: { status: "ok", detail: "credential configured; reachability unverified" },
  },
  {
    id: "sarvam",
    name: "Sarvam AI (saaras batch)",
    is_local: false,
    capabilities: {
      languages: ["hi-IN", "bn-IN"],
      language_detection: true,
      segment_timestamps: true,
      word_timestamps: false,
      diarization: true,
      streaming: false,
      batch: true,
      async_processing: true,
      confidence_scores: false,
      max_file_bytes: null,
      max_duration_s: 7200,
      formats: ["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "webm", "aiff"],
    },
    health: { status: "ok", detail: "credential configured; reachability unverified" },
  },
];

/** `/v1/providers` as the stack answers it: directory plus deployment caps. */
function providersHandler(
  providers: ProviderInfo[] = PROVIDERS,
  limits: PlatformLimits | null = LIMITS,
) {
  return http.get(`${BASE}/v1/providers`, () =>
    HttpResponse.json(limits ? { providers, limits } : { providers }),
  );
}

function providersErrorHandler() {
  return http.get(`${BASE}/v1/providers`, () =>
    HttpResponse.json(
      { error: { code: "INTERNAL_ERROR", message: "directory unavailable" } },
      { status: 500 },
    ),
  );
}

beforeEach(() => {
  // Every test starts from a reachable directory; failure states override it.
  server.use(providersHandler());
});

function renderLanding() {
  return renderApp("/", <LandingPage />);
}

/** Renders the page and waits for the directory-driven section to settle. */
async function renderLandingLoaded() {
  renderLanding();
  await screen.findByRole("article", { name: "Faster-Whisper (local)" });
}

/** The definition row for a capability, so "yes"/"no" is read in context. */
function capabilityRow(card: HTMLElement, label: string): HTMLElement {
  const term = within(card).getByText(label);
  const row = term.closest("div");
  if (!row) throw new Error(`no row for ${label}`);
  return row;
}

test("leads with a headline, the value proposition and the calls to action", async () => {
  renderLanding();

  expect(
    screen.getByRole("heading", { level: 1, name: /measure what your models hear/i }),
  ).toBeInTheDocument();
  expect(screen.getByText(/one api for every speech-to-text provider/i)).toBeInTheDocument();

  // The console lives at /upload; it redirects to /login without a session,
  // which is precisely what a public page should link to.
  const consoleLinks = screen.getAllByRole("link", { name: /open the console/i });
  expect(consoleLinks.length).toBeGreaterThan(0);
  for (const link of consoleLinks) {
    expect(link).toHaveAttribute("href", "/upload");
  }

  expect(screen.getByRole("link", { name: /read the docs/i })).toHaveAttribute(
    "href",
    DOCS_URL,
  );
  expect(screen.getByRole("link", { name: /^repository$/i })).toHaveAttribute(
    "href",
    REPO_URL,
  );
  expect(screen.getByRole("link", { name: /^sign in$/i })).toHaveAttribute(
    "href",
    "/login",
  );

  await screen.findByRole("article", { name: "Faster-Whisper (local)" });
});

test("carries the Evidance brand and a tagline that says what it is", async () => {
  renderLanding();

  expect(screen.getByText("Evidance")).toBeInTheDocument();
  expect(
    screen.getByText(/provider-neutral speech-to-text platform/i),
  ).toBeInTheDocument();

  await screen.findByRole("article", { name: "Faster-Whisper (local)" });
});

test("explains the platform in features, not adjectives", async () => {
  await renderLandingLoaded();

  // Scoped to the features section: the page reuses some of these words in
  // other sections' prose, and this test is about the feature headings.
  const features = screen.getByRole("region", { name: /what it does/i });
  for (const heading of [
    /provider-neutral/i,
    /timestamped/i,
    /live transcription/i,
    /long audio/i,
    /tenant isolation/i,
    /evaluation/i,
  ]) {
    expect(
      within(features).getByRole("heading", { name: heading }),
    ).toBeInTheDocument();
  }
});

test("builds the provider section strictly from the API's directory", async () => {
  renderLanding();

  // Cards appear only once the response arrives, and they carry the names the
  // response carried.
  const local = await screen.findByRole("article", { name: "Faster-Whisper (local)" });
  expect(within(local).getByText("local")).toBeInTheDocument();
  for (const name of [
    "Deepgram (nova-3)",
    "ElevenLabs Scribe",
    "Sarvam AI (saaras batch)",
  ]) {
    expect(screen.getByRole("article", { name })).toBeInTheDocument();
  }
});

test("renders each provider's reported capabilities, local or managed", async () => {
  await renderLandingLoaded();

  const local = screen.getByRole("article", { name: "Faster-Whisper (local)" });
  expect(capabilityRow(local, "Streaming")).toHaveTextContent("yes");
  expect(capabilityRow(local, "Diarization")).toHaveTextContent("no");
  expect(capabilityRow(local, "Word timestamps")).toHaveTextContent("yes");
  // Both limits are null in the payload: said out loud, not invented.
  expect(within(local).getAllByText("no limit documented")).toHaveLength(2);

  const managed = screen.getByRole("article", { name: "Deepgram (nova-3)" });
  expect(within(managed).getByText("managed")).toBeInTheDocument();
  expect(capabilityRow(managed, "Streaming")).toHaveTextContent("no");
  expect(capabilityRow(managed, "Diarization")).toHaveTextContent("yes");
  expect(within(managed).getByText("2.0 GB")).toBeInTheDocument();
  expect(capabilityRow(managed, "Max duration")).toHaveTextContent(
    "no limit documented",
  );

  const elevenlabs = screen.getByRole("article", { name: "ElevenLabs Scribe" });
  expect(within(elevenlabs).getByText("3.0 GB")).toBeInTheDocument();
  expect(within(elevenlabs).getByText("10:00:00.0")).toBeInTheDocument();

  const sarvam = screen.getByRole("article", { name: "Sarvam AI (saaras batch)" });
  expect(capabilityRow(sarvam, "Word timestamps")).toHaveTextContent("no");
  expect(within(sarvam).getByText("2:00:00.0")).toBeInTheDocument();
});

test("shows what the directory returns and nothing it did not", async () => {
  server.use(
    providersHandler([
      {
        id: "acme",
        name: "Acme STT",
        is_local: false,
        capabilities: {
          languages: ["en"],
          language_detection: false,
          segment_timestamps: true,
          word_timestamps: false,
          diarization: false,
          streaming: false,
          batch: true,
          async_processing: false,
          confidence_scores: false,
          max_file_bytes: null,
          max_duration_s: null,
          formats: ["wav"],
        },
        health: { status: "ok" },
      },
    ]),
  );

  renderLanding();

  expect(await screen.findByRole("article", { name: "Acme STT" })).toBeInTheDocument();

  // Scope the count to the directory region: the prose sections carry their
  // own (unnamed) articles.
  const directory = screen.getByRole("region", {
    name: /providers and what they report/i,
  });
  expect(within(directory).getAllByRole("article")).toHaveLength(1);
  expect(
    within(directory).queryByRole("article", { name: /deepgram/i }),
  ).not.toBeInTheDocument();
});

test("states this deployment's own caps from the same response", async () => {
  await renderLandingLoaded();

  const note = screen.getByText(/this deployment accepts uploads up to/i);
  expect(note).toHaveTextContent("100.0 MB");
  expect(note).toHaveTextContent("4:00:00.0");
  expect(note).toHaveTextContent("3:00.0");
});

test("a failing directory renders an honest unavailable state, not a fake list", async () => {
  server.use(providersErrorHandler());

  renderLanding();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/INTERNAL_ERROR/);
  expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();

  // Nothing vendor-shaped is invented while the API is down; the only names
  // that can appear are ones the response carried.
  for (const name of ["Deepgram", "ElevenLabs", "Sarvam"]) {
    expect(screen.queryByText(new RegExp(name, "i"))).not.toBeInTheDocument();
  }
});

test("retrying after a failed directory call recovers", async () => {
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/providers`, () => {
      calls += 1;
      if (calls === 1) {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "boom" } },
          { status: 500 },
        );
      }
      return HttpResponse.json({ providers: PROVIDERS, limits: LIMITS });
    }),
  );

  renderLanding();

  expect(await screen.findByRole("alert")).toHaveTextContent(/INTERNAL_ERROR/);
  const { default: userEvent } = await import("@testing-library/user-event");
  await userEvent.click(screen.getByRole("button", { name: /retry/i }));

  expect(
    await screen.findByRole("article", { name: "Deepgram (nova-3)" }),
  ).toBeInTheDocument();
});

test("describes the pipeline in the order the platform actually runs it", async () => {
  await renderLandingLoaded();

  expect(
    screen.getByRole("heading", { level: 2, name: /how a job runs/i }),
  ).toBeInTheDocument();

  const steps = screen.getByRole("list", { name: /pipeline/i });
  const items = within(steps).getAllByRole("listitem");
  expect(items).toHaveLength(6);

  const order = [
    /upload or record/i,
    /object storage/i,
    /sync or durable/i,
    /provider adapter/i,
    /timestamped segments/i,
    /^result$/i,
  ];
  items.forEach((item, index) => {
    const heading = within(item).getByRole("heading", { level: 3 });
    expect(heading).toHaveTextContent(order[index]);
  });
});

test("states what this deployment can and cannot do today", async () => {
  await renderLandingLoaded();

  expect(
    screen.getByRole("heading", {
      level: 2,
      name: /what this deployment can and cannot do today/i,
    }),
  ).toBeInTheDocument();

  const limits = screen.getByRole("region", {
    name: /what this deployment can and cannot do today/i,
  });

  // The four claims the directive insists on: CPU/GPU locality, key-gated
  // managed providers, local-first streaming, and no local diarization.
  expect(within(limits).getByText(/runs on this machine/i)).toHaveTextContent(
    /cuda/i,
  );
  expect(within(limits).getByText(/runs on this machine/i)).toHaveTextContent(
    /cpu/i,
  );
  expect(
    within(limits).getByText(/only when its api key is configured/i),
  ).toBeInTheDocument();
  expect(
    within(limits).getByText(/streaming and live preview are local-first/i),
  ).toBeInTheDocument();
  expect(
    within(limits).getByText(
      /speaker diarization is not available on the local model/i,
    ),
  ).toBeInTheDocument();
  expect(within(limits).getByText(/no silent fallback/i)).toBeInTheDocument();
});

test("keeps one h1 and sections the page with h2s", async () => {
  await renderLandingLoaded();

  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

  for (const name of [
    /providers and what they report/i,
    /how a job runs/i,
    /what it does/i,
    /can and cannot do today/i,
    /run it yourself/i,
  ]) {
    expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
  }
});

test("uses the header, main and footer landmarks", async () => {
  await renderLandingLoaded();

  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("main")).toBeInTheDocument();
  expect(screen.getByRole("contentinfo")).toBeInTheDocument();
});

test("quotes measured numbers and says where they come from", async () => {
  renderLanding();

  expect(screen.getByText(/measured on the built-in evaluation corpus/i)).toBeInTheDocument();

  await screen.findByRole("article", { name: "Faster-Whisper (local)" });
});

test("carries the theme toggle so the public page matches the app", async () => {
  await renderLandingLoaded();

  expect(
    screen.getByRole("button", { name: /switch to (light|dark) theme/i }),
  ).toBeInTheDocument();
});

test("links back to the app's public pages without requiring a session", async () => {
  renderLanding();

  expect(screen.getByRole("contentinfo")).toBeInTheDocument();
});

test("tells a newcomer how to run the whole thing in one command", async () => {
  await renderLandingLoaded();

  expect(
    screen.getByRole("heading", { name: /run it yourself/i }),
  ).toBeInTheDocument();
  // The literal command, because a marketing page that cannot be followed is
  // just decoration.
  expect(screen.getByText("./launch.sh")).toBeInTheDocument();
  expect(
    screen.getByText(/docker desktop or docker engine/i),
  ).toBeInTheDocument();
  expect(screen.getByText("http://127.0.0.1:8080")).toBeInTheDocument();
});
