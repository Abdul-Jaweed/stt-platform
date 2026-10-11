import {
  ArrowRight,
  Eye,
  EyeSlash,
  SignIn,
  WarningCircle,
} from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { useApi } from "../api/context";
import { useSession } from "../auth/context";
import BrandMark from "../components/BrandMark";
import ThemeToggle from "../components/ThemeToggle";

const APP_HOME = "/upload";

/** Only a path inside this app may be a redirect target. */
export function safeNext(raw: string | null): string {
  if (!raw) return APP_HOME;
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) {
    return APP_HOME;
  }
  return raw;
}

export default function LoginPage() {
  const { login, loginError, authenticated, status } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<{
    username?: string;
    password?: string;
  }>({});
  // null = not answered yet. The hint below must not guess: claiming the
  // development defaults are in use when they are not is both wrong and a
  // small leak, and a deployment with generated credentials would see it.
  const [devCredentials, setDevCredentials] = useState<boolean | null>(null);
  const api = useApi();

  const next = safeNext(params.get("next"));

  useEffect(() => {
    let cancelled = false;
    void api
      .getAuthConfig()
      .then((config) => {
        if (!cancelled) setDevCredentials(config.dev_credentials);
      })
      .catch(() => {
        // No answer means no claim: silence is the safe default here.
        if (!cancelled) setDevCredentials(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (status === "ready" && authenticated) {
    return <Navigate to={next} replace />;
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const errors: { username?: string; password?: string } = {};
    if (!username.trim()) errors.username = "Enter your username.";
    if (!password) errors.password = "Enter your password.";
    setFieldErrors(errors);
    if (errors.username || errors.password) return;

    setSubmitting(true);
    const ok = await login(username.trim(), password);
    setSubmitting(false);
    if (ok) navigate(next, { replace: true });
  }

  return (
    <div className="auth-page">
      <header className="auth-header">
        <Link className="brand" to="/">
          <span className="brand-mark" aria-hidden="true">
            <BrandMark />
          </span>
          <span className="brand-text">
            <span className="brand-name">STT Platform</span>
            <span className="brand-tag">provider-neutral transcription</span>
          </span>
        </Link>
        <ThemeToggle />
      </header>

      <main className="auth-main">
        <form className="card auth-card" onSubmit={onSubmit} noValidate>
          <h1 className="auth-title">Sign in</h1>
          <p className="auth-sub">
            The console needs a session; your browser keeps it in an HttpOnly
            cookie and never sees an API key.
          </p>

          {loginError && (
            <p className="form-error" role="alert">
              <WarningCircle size={16} weight="bold" aria-hidden="true" />
              <span>{loginError}</span>
            </p>
          )}

          <label className="field">
            <span className="field-label">Username</span>
            <input
              className="input"
              name="username"
              autoComplete="username"
              autoFocus
              value={username}
              aria-invalid={Boolean(fieldErrors.username)}
              onChange={(event) => setUsername(event.target.value)}
            />
            {fieldErrors.username && (
              <span className="field-error">{fieldErrors.username}</span>
            )}
          </label>

          <label className="field">
            <span className="field-label">Password</span>
            <span className="input-with-action">
              <input
                className="input"
                name="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                aria-invalid={Boolean(fieldErrors.password)}
                onChange={(event) => setPassword(event.target.value)}
              />
              <button
                type="button"
                className="input-action"
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? (
                  <EyeSlash size={18} aria-hidden="true" />
                ) : (
                  <Eye size={18} aria-hidden="true" />
                )}
              </button>
            </span>
            {fieldErrors.password && (
              <span className="field-error">{fieldErrors.password}</span>
            )}
          </label>

          <button className="button primary auth-submit" type="submit" disabled={submitting}>
            <SignIn size={18} weight="bold" aria-hidden="true" />
            {submitting ? "Signing in…" : "Sign in"}
          </button>

          {devCredentials === true && (
            <p className="hint auth-hint">
              Development default credentials are <code>stt</code> /{" "}
              <code>stt</code>. Change them before deploying — the server
              refuses the default once it runs in production mode.
            </p>
          )}
          <p className="hint">
            <Link to="/">Back to the overview</Link>
          </p>
        </form>
      </main>

      <footer className="auth-footer">
        <ArrowRight size={14} aria-hidden="true" />
        <span>
          Signing in opens the console; the public overview stays one click
          away.
        </span>
      </footer>
    </div>
  );
}
