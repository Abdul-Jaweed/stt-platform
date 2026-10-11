import { useState } from "react";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type { EvaluationRecord } from "../api/types";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; data: EvaluationRecord }
  | { kind: "unavailable" }
  | { kind: "error"; code: string; message: string };

export default function EvaluationsPage() {
  const api = useApi();
  const [id, setId] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function load() {
    const trimmed = id.trim();
    if (!trimmed) return;
    setState({ kind: "loading" });
    try {
      const data = await api.getEvaluation(trimmed);
      setState({ kind: "loaded", data });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // The endpoint does not exist yet — say so, invent nothing.
        setState({ kind: "unavailable" });
      } else if (err instanceof ApiError) {
        setState({ kind: "error", code: err.code, message: err.message });
      } else {
        setState({
          kind: "error",
          code: "UNKNOWN_ERROR",
          message: String(err),
        });
      }
    }
  }

  return (
    <section className="page evaluations-page">
      <h2>Evaluations</h2>
      <div className="field">
        <label htmlFor="evaluation-id">Evaluation ID</label>
        <input
          id="evaluation-id"
          value={id}
          onChange={(event) => setId(event.target.value)}
        />
        <button type="button" onClick={() => void load()}>
          Load
        </button>
      </div>

      {state.kind === "loading" && <p className="hint">Loading…</p>}
      {state.kind === "unavailable" && (
        <p className="hint">
          Evaluations are not part of the API yet — GET /v1/evaluations
          arrives in Phase 6. Nothing is shown rather than showing invented
          numbers.
        </p>
      )}
      {state.kind === "loaded" && (
        <pre>{JSON.stringify(state.data, null, 2)}</pre>
      )}
      {state.kind === "error" && (
        <p role="alert" className="error">
          <strong>{state.code}</strong> {state.message}
        </p>
      )}
    </section>
  );
}
