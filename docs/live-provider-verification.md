# Live Provider Verification (2026-10-11)

The managed adapters were exercised once against the real vendors with
free-tier keys, on the 10-second `audio/sample.wav`
(320,044 bytes, sha256 `251082a3…cbf8e4`), **speech-to-text endpoints only**.

## Paid-call accounting (free tier discipline)

| Call | Endpoint | Outcome |
|---|---|---|
| Deepgram #1 | `POST /v1/listen` | succeeded; exposed a missing `detect_language` (see below) |
| Deepgram #2 | `POST /v1/listen` | **verification run** |
| ElevenLabs #1 | `POST /v1/speech-to-text` | **verification run** |
| Sarvam job #1 | batch `20261011_ab2f261d…` | **verification run** (pytest; see the exit-hang note) |
| Sarvam job #2 | batch `20261011_d506f859…` | diagnostic re-run after the corrected request (see below) |

Five paid operations in total, each on 10 s of audio. No retries on
success, no endpoint probing, no non-STT model touched.

## Results

| | Deepgram | ElevenLabs | Sarvam |
|---|---|---|---|
| Model | `nova-3` (version `2025-07-31.0`) | `scribe_v2` | `saaras:v4` |
| Mode | sync | sync | batch job (submit → poll → fetch) |
| Latency | 1.93 s | 1.51 s | 6.45 s / ~20 s (2 jobs) |
| Duration reported | 10.0 s (vendor) | 10.0 s (local probe) | 10.0 s (local probe) |
| Segments | 2 (utterances) | 1 (derived from word runs) | 1 (chunk timestamps) |
| Words | 25 | 27 | — (`words_available: false`) |
| Word confidence | 0.647–0.989 | `null` (vendor reports none) | — |
| Speakers | `[0]` | — (diarization not yet requested at run time) | `[0]` |
| Language | `en` (detected) | `eng` | `en-IN` |
| Warnings | none | none | none |

Transcripts of the same audio (different vendors, different text — this is
exactly what the Phase-5 evaluation harness will score):

- **Deepgram**: "Hello. I'm Abdul Jawid, AI engineer at Home and Manage.
  And, currently, I'm working multiple projects projects like, mass
  platform, basically memory as a service..."
- **ElevenLabs**: "Hello, I'm Abdul Javed, uh, AI engineer at Homerun
  Manage, and, uh, currently I'm working multiple projects. Projects like,
  uh, maths platform, basically memory as a service"
- **Sarvam**: "Hello, I am Abdul Javed, AI engineer at Human Manage, and
  currently I am working on multiple projects, projects like a matched
  platform, basically memory as a service."

Evidence (git-ignored, on disk): `results/live_providers/result.json`
(Deepgram, ElevenLabs, first Sarvam run) and
`results/live_providers/sarvam_live.json` +
`results/live_providers/sarvam_poll.log` (second Sarvam run, including
every raw status payload).

## Defects the live runs found (all fixed under TDD)

1. **Deepgram never requested language detection.** The adapter mapped
   `results.channels[0].detected_language`, but the vendor only returns
   that field when `detect_language=true` is sent — so `language` was
   silently `None` for every auto-detect request. Fixed: `detect_language=true`
   is sent whenever the caller does not pin a language
   (`test_language_param_omitted_when_not_requested`,
   `test_detected_language_mapped_into_transcript`).
2. **Deepgram advertised diarization without asking for it.** The
   capability said `True` while the request carried no `diarize` parameter.
   Fixed: `diarize=true` is requested and the capability is now verified
   (speakers came back).
3. **ElevenLabs advertised diarization without asking for it.** Fixed the
   same way (`diarize=true`); the capability stays `documented` until a
   live run exercises it.
4. **Sarvam read the wrong per-file field.** `job_details[]` entries carry
   `state` (`Success` / `API Error` / `Internal Server Error`), not
   `status: "success"` — a failed file's output could have been downloaded.
   Fixed: only `Success` entries are considered
   (`test_poll_completed_skips_failed_files`).
5. **Sarvam omitted the documented `mode`.** The batch API takes an
   explicit output mode; `mode=transcribe` is now always sent instead of
   relying on a server default (`test_submit_requests_transcribe_mode`).

## Provenance after this run

`capability_sources` in each adapter was flipped from `documented` to
`verified` **only** where a live assertion proved it, and the flip is
pinned by `tests/unit/test_capability_provenance.py`:

| Provider | verified | still documented |
|---|---|---|
| Deepgram | word + segment timestamps, diarization, confidence, language detection | — |
| ElevenLabs | word timestamps, language detection | diarization (requested, not yet exercised live) |
| Sarvam | async processing, segment timestamps, diarization, language detection | — |

## How to re-run (costs money)

```bash
cd backend
uv run pytest -m live_provider -q -s            # every configured provider
uv run pytest -m live_provider -q -s -k deepgram   # one provider
```

Keys come from `backend/.env` (`STT_<PROVIDER>_API_KEY`); without a key
each test skips. The suite is deselected in ordinary gates, writes evidence
**before** asserting (a failed assertion still leaves the payload on disk),
and never retries a successful call.

**Windows note:** the pytest process itself can hang after the test body
has finished (observed once with the Sarvam run: the job completed, the
evidence file was written, yet the shell never returned). The evidence file
is therefore authoritative; run one provider per invocation and give the
call a generous timeout.
