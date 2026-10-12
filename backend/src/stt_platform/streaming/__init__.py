"""Streaming transcription (Phase 8): session transport, VAD, decoding.

Everything here is local-only by design; the WebSocket endpoint that consumes
it lives in ``api/v1/streaming.py``. See ``docs/superpowers/plans/
2026-10-12-phase-8-streaming.md`` for the contract.
"""
