import {
  ArrowRight,
  ArrowsClockwise,
  BookOpen,
  Broadcast,
  ChartBar,
  Check,
  CheckCircle,
  ClockCounterClockwise,
  Cloud,
  Cpu,
  Database,
  FileAudio,
  FileText,
  GithubLogo,
  Lightning,
  ListChecks,
  Microphone,
  Minus,
  PlugsConnected,
  ShieldCheck,
  SignIn,
  StackSimple,
  Waveform,
  WarningCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type { PlatformLimits, ProviderInfo } from "../api/types";
import BrandMark from "../components/BrandMark";
import ThemeToggle from "../components/ThemeToggle";
import { formatBytes, formatDuration } from "../lib/format";

/**
 * The public landing page for Evidance.
 *
 * Everything vendor-shaped on this page comes from `GET /v1/providers` on
 * load: the providers listed, their limits and their capabilities. If the API
 * cannot be reached, the page says so instead of drawing a directory from
 * memory. The prose sections describe this repository and this deployment,
 * not an aspiration.
 */

const DOCS_URL = "https://github.com/Abdul-Jaweed/stt-platform/tree/main/docs";
const REPO_URL = "https://github.com/Abdul-Jaweed/stt-platform";

interface LoadError {
  code: string;
  message: string;
}

const FEATURES = [
  {
    icon: PlugsConnected,
    title: "Provider-neutral by construction",
    body: "Local Faster-Whisper and three managed vendors sit behind one internal contract. Swapping providers changes a form field, not your integration.",
  },
  {
    icon: ClockCounterClockwise,
    title: "Timestamped, segment by segment",
    body: "Segments and word-level timings on the original audio timeline — never a fabricated offset, and never a guess at confidence.",
  },
  {
    icon: Microphone,
    title: "Live transcription while you record",
    body: "The audio captured so far is transcribed as you speak, on the local model, with nothing stored until you submit the take.",
  },
  {
    icon: StackSimple,
    title: "Long audio that survives interruption",
    body: "Jobs run as durable workflows with retries, cancellation and recovery bounds: kill the worker mid-inference and the job still finishes.",
  },
  {
    icon: ShieldCheck,
    title: "Tenant isolation and retention",
    body: "The scope comes from the presented credential, never the request. Deletion removes objects before rows, and expired audio is purged on demand.",
  },
  {
    icon: ChartBar,
    title: "Evaluation, not vibes",
    body: "WER and CER against human-verified references, cross-checked with an independent library and logged to MLflow — a metric that could not be measured is omitted, never zero.",
  },
];

const PIPELINE = [
  {
    icon: FileAudio,
    title: "Upload or record",
    body: "A file or a browser recording is validated by content — format, duration, size — against this deployment's caps before anything else happens.",
  },
  {
    icon: Database,
    title: "Store in object storage",
    body: "Accepted audio is written to object storage under an opaque key before transcription starts; deletion removes the objects before the database rows.",
  },
  {
    icon: Broadcast,
    title: "Sync or durable",
    body: "Short audio is transcribed in the request that uploaded it. Longer audio becomes a durable Temporal workflow — retries, cancellation and recovery bounds included, with a status trail the console can poll.",
  },
  {
    icon: Cpu,
    title: "Provider adapter",
    body: "The provider named on the request runs through its adapter: the local model on this machine, or a managed vendor API when its key is configured. There is no silent fallback.",
  },
  {
    icon: ListChecks,
    title: "Timestamped segments",
    body: "Segments come back on the original audio timeline, with words where the provider reports them; degenerate spans are dropped, never clamped.",
  },
  {
    icon: FileText,
    title: "Result",
    body: "The transcript is served over the public API with language, timings and provider metadata, and exports as TXT, SRT or VTT.",
  },
];

const CAN_DO = [
  "The local model runs on this machine — CUDA when a GPU is passed through, CPU otherwise — so locally transcribed audio never leaves the host.",
  "Segment timestamps from every provider; word timestamps wherever the provider's card above reports them.",
  "Durable jobs with retries, cancellation and a pollable status trail, plus in-request transcription while you wait for short audio.",
  "Evaluation with WER and CER against human-verified references — a metric that cannot be measured is omitted, never zero.",
  "Provider choice is explicit per request, and the directory above shows what each provider actually reports.",
];

const CANNOT_DO = [
  "A managed provider is registered only when its API key is configured; without one it does not appear above and cannot be selected.",
  "Speaker diarization is not available on the local model; only providers whose cards say so return it.",
  "Streaming and live preview are local-first in this build; the managed providers here are batch-only.",
  "No silent fallback: a job runs on the provider you name or it fails — audio kept local is never re-routed to a vendor.",
  "Speech-to-text only: transcripts with timings, no translation and no speech synthesis.",
];

/** A yes/no cell where the word carries the meaning and the icon is decoration. */
function Capability({ value }: { value: boolean }) {
  return (
    <span className={value ? "cap cap-yes" : "cap cap-no"}>
      {value ? (
        <Check size={14} weight="bold" aria-hidden="true" />
      ) : (
        <Minus size={14} aria-hidden="true" />
      )}
      {value ? "yes" : "no"}
    </span>
  );
}

function ProviderCard({ provider }: { provider: ProviderInfo }) {
  const headingId = `provider-${provider.id}-name`;
  const { capabilities } = provider;

  return (
    <article className="provider-card" aria-labelledby={headingId}>
      <div className="provider-card-head">
        <h3 id={headingId}>{provider.name}</h3>
        <span
          className={
            provider.is_local
              ? "provider-badge provider-badge-local"
              : "provider-badge provider-badge-managed"
          }
        >
          {provider.is_local ? (
            <Cpu size={14} weight="bold" aria-hidden="true" />
          ) : (
            <Cloud size={14} weight="bold" aria-hidden="true" />
          )}
          {provider.is_local ? "local" : "managed"}
        </span>
      </div>
      <dl className="provider-facts">
        <div>
          <dt>Word timestamps</dt>
          <dd>
            <Capability value={capabilities.word_timestamps} />
          </dd>
        </div>
        <div>
          <dt>Diarization</dt>
          <dd>
            <Capability value={capabilities.diarization} />
          </dd>
        </div>
        <div>
          <dt>Streaming</dt>
          <dd>
            <Capability value={capabilities.streaming} />
          </dd>
        </div>
        <div>
          <dt>Max file</dt>
          <dd>
            {capabilities.max_file_bytes != null
              ? formatBytes(capabilities.max_file_bytes)
              : "no limit documented"}
          </dd>
        </div>
        <div>
          <dt>Max duration</dt>
          <dd>
            {capabilities.max_duration_s != null
              ? formatDuration(capabilities.max_duration_s)
              : "no limit documented"}
          </dd>
        </div>
        <div>
          <dt>Languages</dt>
          <dd>{capabilities.languages.length}</dd>
        </div>
      </dl>
    </article>
  );
}

export default function LandingPage() {
  const api = useApi();
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [limits, setLimits] = useState<PlatformLimits | null>(null);
  const [error, setError] = useState<LoadError | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setProviders(null);
    try {
      const directory = await api.getProviderDirectory();
      setProviders(directory.providers);
      setLimits(directory.limits ?? null);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { code: err.code, message: err.message }
          : { code: "UNKNOWN_ERROR", message: String(err) },
      );
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="landing">
      <header className="landing-header">
        <Link className="brand" to="/">
          <span className="brand-mark" aria-hidden="true">
            <BrandMark />
          </span>
          <span className="brand-text">
            <span className="brand-name">Evidance</span>
            <span className="brand-tag">Provider-neutral speech-to-text platform</span>
          </span>
        </Link>
        <nav className="landing-nav" aria-label="Sections">
          <a href="#providers">Providers</a>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#limits">Limits</a>
          <a href="#quickstart">Quick start</a>
        </nav>
        <div className="landing-actions">
          <ThemeToggle />
          <Link className="button ghost" to="/login">
            <SignIn size={16} weight="bold" aria-hidden="true" />
            Sign in
          </Link>
        </div>
      </header>

      <main>
        <section className="hero">
          <p className="eyebrow">
            <Waveform size={16} weight="bold" aria-hidden="true" />
            Speech-to-text you can measure
          </p>
          <h1>Measure what your models hear.</h1>
          <p className="hero-sub">
            One API for every speech-to-text provider — local GPU or managed —
            returning timestamped transcripts you can evaluate, with the numbers
            to prove the choice.
          </p>
          <div className="hero-actions">
            <Link className="button primary" to="/upload">
              Open the console
              <ArrowRight size={16} weight="bold" aria-hidden="true" />
            </Link>
            <a
              className="button ghost"
              href={DOCS_URL}
              target="_blank"
              rel="noreferrer"
            >
              <BookOpen size={16} weight="bold" aria-hidden="true" />
              Read the docs
            </a>
            <a
              className="button ghost"
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
            >
              <GithubLogo size={16} weight="bold" aria-hidden="true" />
              Repository
            </a>
          </div>
          <dl className="hero-stats">
            <div>
              <dt>0.100</dt>
              <dd>
                WER micro, measured on the built-in evaluation corpus (8 clips,
                large-v3-turbo, CUDA)
              </dd>
            </div>
            <div>
              <dt>0.072</dt>
              <dd>real-time factor on an RTX 4060</dd>
            </div>
          </dl>
        </section>

        <section
          className="landing-section"
          id="providers"
          aria-labelledby="providers-heading"
        >
          <h2 id="providers-heading">Providers and what they report</h2>
          <p className="section-sub">
            The list below is the live answer from this deployment's API: the
            providers registered right now, each with the capabilities it
            reports. Nothing here assumes a provider can do more than it said.
          </p>

          {error ? (
            <div className="directory-error">
              <p role="alert" className="error">
                <strong>{error.code}</strong> {error.message}
              </p>
              <p className="hint">
                The provider directory could not be loaded, so no provider list
                is shown. A list the API did not return would be worse than
                none.
              </p>
              <button type="button" className="ghost" onClick={() => void load()}>
                <ArrowsClockwise size={14} aria-hidden="true" />
                Retry
              </button>
            </div>
          ) : !providers ? (
            <p className="hint">Loading the provider directory…</p>
          ) : (
            <>
              {providers.length === 0 ? (
                <p className="hint">
                  The directory answered, but no providers are registered in
                  this deployment.
                </p>
              ) : (
                <div className="provider-grid">
                  {providers.map((provider) => (
                    <ProviderCard key={provider.id} provider={provider} />
                  ))}
                </div>
              )}
              {limits && (
                <p className="hint landing-caps-note">
                  {`This deployment accepts uploads up to ${formatBytes(limits.max_upload_bytes)} and jobs up to ${formatDuration(limits.max_async_duration_s)}; the first ${formatDuration(limits.max_sync_duration_s)} of audio is transcribed while you wait, and live preview stops at ${formatDuration(limits.max_live_preview_duration_s)}.`}
                </p>
              )}
            </>
          )}
        </section>

        <section
          className="landing-section"
          id="how"
          aria-labelledby="how-heading"
        >
          <h2 id="how-heading">How a job runs</h2>
          <p className="section-sub">
            From an upload to a timestamped transcript, in the order the
            platform actually runs it.
          </p>
          <ol className="step-grid" aria-label="Pipeline">
            {PIPELINE.map(({ icon: Icon, title, body }, index) => (
              <li className="step-card" key={title}>
                <span className="step-number" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="feature-icon" aria-hidden="true">
                  <Icon size={20} weight="bold" />
                </span>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section
          className="landing-section"
          id="features"
          aria-labelledby="features-heading"
        >
          <h2 id="features-heading">What it does</h2>
          <p className="section-sub">
            The parts that decide whether a transcript is usable — and whether
            you can defend the provider you picked.
          </p>
          <div className="feature-grid">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <article className="feature-card" key={title}>
                <span className="feature-icon" aria-hidden="true">
                  <Icon size={22} weight="bold" />
                </span>
                <h3>{title}</h3>
                <p>{body}</p>
              </article>
            ))}
          </div>
        </section>

        <section
          className="landing-section"
          id="limits"
          aria-labelledby="limits-heading"
        >
          <h2 id="limits-heading">
            What this deployment can and cannot do today
          </h2>
          <p className="section-sub">
            A limit found in production costs more than one read here. Both
            columns describe this deployment as it is running now.
          </p>
          <div className="limits-grid">
            <div className="limits-column">
              <h3>
                <CheckCircle size={18} weight="bold" aria-hidden="true" />
                Works today
              </h3>
              <ul>
                {CAN_DO.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
            <div className="limits-column">
              <h3>
                <WarningCircle size={18} weight="bold" aria-hidden="true" />
                Limits and requirements
              </h3>
              <ul>
                {CANNOT_DO.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section
          className="landing-section quick-start"
          id="quickstart"
          aria-labelledby="quickstart-heading"
        >
          <h2 id="quickstart-heading">Run it yourself</h2>
          <p className="section-sub">
            Docker Desktop or Docker Engine with compose v2, and one command.
            The stack brings its own database, object storage, workflow engine
            and console — no cloud account, no API key.
          </p>
          <div className="quick-start-grid">
            <div className="quick-start-command">
              <pre>
                <code>./launch.sh</code>
              </pre>
              <p className="hint">
                On a GPU host <code>./launch.sh gpu</code> switches the local
                model to CUDA; <code>./launch.sh help</code> lists the rest.
                First run generates the credentials it needs into a gitignored
                <code> .env</code>.
              </p>
            </div>
            <dl className="quick-start-facts">
              <div>
                <dt>Console</dt>
                <dd>
                  <code>http://127.0.0.1:8080</code>
                </dd>
              </div>
              <div>
                <dt>API</dt>
                <dd>
                  <code>http://127.0.0.1:8200/v1</code>
                </dd>
              </div>
              <div>
                <dt>Workflows</dt>
                <dd>
                  <code>http://127.0.0.1:28080</code>
                </dd>
              </div>
            </dl>
          </div>
        </section>

        <section className="cta-band">
          <Lightning size={22} weight="fill" aria-hidden="true" />
          <h2>Start transcribing</h2>
          <p>
            Bring audio; the platform brings the timestamps, the retries and the
            evidence.
          </p>
          <Link className="button primary" to="/upload">
            Open the console
            <ArrowRight size={16} weight="bold" aria-hidden="true" />
          </Link>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-footer-inner">
          <p>
            Evidance — a speech-to-text service with evaluation built in, over a
            local model and managed providers. The console consumes only the
            public <code>/v1</code> API.
          </p>
          <p className="landing-footer-links">
            <a href={REPO_URL} target="_blank" rel="noreferrer">
              <GithubLogo size={16} aria-hidden="true" />
              Source
            </a>
            <a href={DOCS_URL} target="_blank" rel="noreferrer">
              <BookOpen size={16} aria-hidden="true" />
              Docs
            </a>
            <Link to="/upload">Open the console</Link>
          </p>
        </div>
      </footer>
    </div>
  );
}
