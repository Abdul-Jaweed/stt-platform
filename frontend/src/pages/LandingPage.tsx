import {
  ArrowRight,
  Broadcast,
  ChartBar,
  ClockCounterClockwise,
  Cpu,
  FileAudio,
  GithubLogo,
  Lightning,
  ListChecks,
  Microphone,
  PlugsConnected,
  ShieldCheck,
  SignIn,
  StackSimple,
  Waveform,
} from "@phosphor-icons/react";
import { Link } from "react-router-dom";

import BrandMark from "../components/BrandMark";
import ThemeToggle from "../components/ThemeToggle";

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

const STEPS = [
  {
    icon: FileAudio,
    title: "Upload or record",
    body: "A file or a take from the browser travels the same path: validated by content, stored under an opaque key.",
  },
  {
    icon: Broadcast,
    title: "Queue and route",
    body: "Short audio is transcribed in-request; long audio becomes a durable job with a lifecycle trail you can watch.",
  },
  {
    icon: Cpu,
    title: "Transcribe",
    body: "A local GPU model by default, or a managed API when the request asks for one and the key is configured.",
  },
  {
    icon: ListChecks,
    title: "Measure",
    body: "Compare providers on the same corpus, export TXT/SRT/VTT, and keep the numbers that justify the choice.",
  },
];

const PROVIDERS = [
  { name: "Faster-Whisper", detail: "large-v3-turbo, local CUDA", local: true },
  { name: "Deepgram", detail: "Nova-3, pre-recorded", local: false },
  { name: "ElevenLabs", detail: "Scribe v2", local: false },
  { name: "Sarvam", detail: "Saaras v4, batch", local: false },
];

export default function LandingPage() {
  return (
    <div className="landing">
      <header className="landing-header">
        <Link className="brand" to="/">
          <span className="brand-mark" aria-hidden="true">
            <BrandMark />
          </span>
          <span className="brand-text">
            <span className="brand-name">STT Platform</span>
            <span className="brand-tag">provider-neutral transcription</span>
          </span>
        </Link>
        <nav className="landing-nav" aria-label="Sections">
          <a href="#quickstart">Quick start</a>
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#providers">Providers</a>
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
            <Link className="button primary" to="/login">
              Get started
              <ArrowRight size={16} weight="bold" aria-hidden="true" />
            </Link>
          </div>
          <dl className="hero-stats">
            <div>
              <dt>4</dt>
              <dd>providers behind one contract</dd>
            </div>
            <div>
              <dt>0.100</dt>
              <dd>WER, measured on the built-in evaluation corpus</dd>
            </div>
            <div>
              <dt>0.072</dt>
              <dd>real-time factor on an RTX 4060</dd>
            </div>
            <div>
              <dt>100%</dt>
              <dd>segments with usable timestamps</dd>
            </div>
          </dl>
        </section>

        <section className="landing-section quick-start" id="quickstart">
          <h2>Run it yourself</h2>
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

        <section className="landing-section" id="features">
          <h2>What it does</h2>
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

        <section className="landing-section" id="how">
          <h2>How a job runs</h2>
          <p className="section-sub">
            From a browser take to a measurable result, with every step
            observable.
          </p>
          <ol className="step-grid">
            {STEPS.map(({ icon: Icon, title, body }, index) => (
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

        <section className="landing-section" id="providers">
          <h2>Providers</h2>
          <p className="section-sub">
            Capabilities are discovered from each adapter, never assumed: the
            console marks what a live run has actually proven.
          </p>
          <ul className="provider-strip">
            {PROVIDERS.map(({ name, detail, local }) => (
              <li key={name}>
                <span className="provider-name">
                  {local ? (
                    <Cpu size={16} weight="bold" aria-hidden="true" />
                  ) : (
                    <Broadcast size={16} weight="bold" aria-hidden="true" />
                  )}
                  {name}
                </span>
                <span className="provider-detail">{detail}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="cta-band">
          <Lightning size={22} weight="fill" aria-hidden="true" />
          <h2>Start transcribing</h2>
          <p>
            Bring audio; the platform brings the timestamps, the retries and the
            evidence.
          </p>
          <Link className="button primary" to="/login">
            Open the console
            <ArrowRight size={16} weight="bold" aria-hidden="true" />
          </Link>
        </section>
      </main>

      <footer className="landing-footer">
        <div className="landing-footer-inner">
          <p>
            STT Platform — a provider-neutral speech-to-text service with
            evaluation built in. The console consumes only the public{" "}
            <code>/v1</code> API.
          </p>
          <p className="landing-footer-links">
            <a href="https://github.com/Abdul-Jaweed/stt-platform">
              <GithubLogo size={16} aria-hidden="true" />
              Source
            </a>
            <Link to="/login">Open the console</Link>
          </p>
        </div>
      </footer>
    </div>
  );
}
