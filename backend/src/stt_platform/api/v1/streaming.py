"""WS /v1/transcriptions/stream -- true streaming transcription, local-first.

A stream is a conversation, not a job. The client sends audio; the server
answers with partials while the utterance is still open and a final when it
closes, each stamped on the session's own timeline. **Nothing is persisted**:
no job row, no object, no trail. When the take is worth keeping, the client
sends the same audio through ``POST /v1/transcriptions`` -- which is what the
console does when you press Stop.

Three red lines from the spec live here:

* a managed provider is refused (``STREAM_LOCAL_ONLY``) before a single audio
  byte is accepted -- local audio never gets routed to a cloud vendor by
  surprise;
* the tenant comes from the same server-side credential as the REST API
  (session cookie or API key), never from anything the client sends;
* a session that stops hearing anything ends itself, and one that runs past
  its cap is closed (``capped`` then ``done``), so a forgotten tab cannot
  hold a GPU slot forever.

Threading: the event loop owns the socket, one worker thread owns the session.
The worker decodes, segments, transcribes and pushes wire frames onto an
asyncio queue with ``call_soon_threadsafe``; the loop sends them in order.
Only the worker ever touches the session, which is what keeps the session a
plain synchronous object with no locks in it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, WebSocket

from stt_platform.api.security import get_scope
from stt_platform.core.config import Settings
from stt_platform.domain.errors import DomainError, ProviderNotFound, Unauthorized
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.services.streaming import (
    STREAM_BAD_REQUEST,
    STREAM_BUSY,
    STREAM_DECODE_FAILED,
    STREAM_INTERNAL,
    STREAM_LOCAL_ONLY,
    STREAM_SAMPLE_RATE,
    ProviderWindowTranscriber,
    StreamLimiter,
    as_window_provider,
    stream_config,
)
from stt_platform.streaming.decoder import DecodeError, FfmpegPcmDecoder
from stt_platform.streaming.session import StreamEvent, StreamingSession

log = logging.getLogger(__name__)

router = APIRouter(prefix="/transcriptions", tags=["streaming"])

#: How long the worker may take to flush a stopped stream before we give up.
_DRAIN_TIMEOUT_S = 30.0
#: The worker's tick: a partial, a cap or an idle end is noticed this fast.
_POLL_INTERVAL_S = 0.05
_INPUT_FORMATS = {"container", "pcm16"}
_END = object()  # queue sentinel: the worker will never produce more frames

_WS_UNAUTHORIZED = 4401
_WS_POLICY = 1008
_WS_TRY_LATER = 1013


@router.websocket("/stream")
async def stream_transcription(websocket: WebSocket) -> None:
    try:
        scope = get_scope(websocket)
    except Unauthorized as exc:
        # Before accept(): Starlette answers the handshake with 403.
        log.info("stream refused: %s", exc.message)
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    settings: Settings = websocket.app.state.settings
    limiter: StreamLimiter | None = getattr(websocket.app.state, "stream_limiter", None)
    if limiter is not None and not limiter.try_acquire():
        await websocket.accept()
        await _fail(
            websocket,
            STREAM_BUSY,
            f"this server is already streaming {limiter.limit} session(s); "
            "try again when one ends",
            close_code=_WS_TRY_LATER,
        )
        return

    try:
        await _serve(websocket, settings=settings, scope=scope)
    except Exception:  # pragma: no cover - defensive: a bug must not leak a traceback
        log.exception("stream session failed")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
    finally:
        if limiter is not None:
            limiter.release()


async def _serve(websocket: WebSocket, *, settings: Settings, scope: str) -> None:
    await websocket.accept()
    registry: ProviderRegistry = websocket.app.state.registry

    message = await websocket.receive()
    if message.get("type") == "websocket.disconnect":
        return
    try:
        start, first_chunk = _parse_start(message)
    except DomainError as exc:
        await _fail(websocket, STREAM_BAD_REQUEST, exc.message)
        return

    try:
        provider = registry.get(start.get("provider") or None)
    except ProviderNotFound as exc:
        await _fail(websocket, STREAM_BAD_REQUEST, exc.message)
        return
    if not provider.is_local:
        await _fail(
            websocket,
            STREAM_LOCAL_ONLY,
            f"streaming is offered by local providers only; {provider.id!r} is a "
            "managed provider -- send the recording to POST /v1/transcriptions "
            "and let the job route it",
        )
        return
    window_provider = as_window_provider(provider)
    if window_provider is None:
        await _fail(
            websocket,
            STREAM_BAD_REQUEST,
            f"provider {provider.id!r} has no in-memory window API to stream with",
        )
        return

    input_format = str(start.get("input_format") or "container").lower()
    if input_format not in _INPUT_FORMATS:
        await _fail(
            websocket,
            STREAM_BAD_REQUEST,
            f"input_format must be one of {sorted(_INPUT_FORMATS)}",
        )
        return
    language = start.get("language") or None
    if language is not None and not isinstance(language, str):
        await _fail(websocket, STREAM_BAD_REQUEST, "language must be a string or null")
        return
    if first_chunk is not None and len(first_chunk) > settings.stream_max_frame_bytes:
        await _fail(websocket, STREAM_BAD_REQUEST, _too_big(len(first_chunk), settings))
        return

    session_id = uuid.uuid4().hex
    session = StreamingSession(
        transcriber=ProviderWindowTranscriber(
            window_provider, model_name=settings.whisper_model
        ),
        config=stream_config(settings, language=language),
        session_id=session_id,
    )
    decoder = FfmpegPcmDecoder(raw_pcm16=(input_format == "pcm16"))
    decoder.start()
    log.info(
        "stream session started id=%s provider=%s scope=%s format=%s",
        session_id[:8],
        provider.id,
        scope,
        input_format,
    )

    await websocket.send_json(
        session.ready_payload()
        | {
            "type": "ready",
            "sample_rate": STREAM_SAMPLE_RATE,
            "input_format": input_format,
        }
    )

    frames: asyncio.Queue[Any] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    abort = threading.Event()

    def emit(events: list[StreamEvent]) -> None:
        for event in events:
            emit_frame(event.to_wire())

    def emit_frame(frame: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(frames.put_nowait, frame)

    def close_decoder(*, report: bool) -> list[StreamEvent]:
        """Reap ffmpeg and hand over whatever it still had buffered."""
        try:
            decoder.close()
        except DecodeError as exc:
            if report:
                emit_frame(
                    {
                        "type": "error",
                        "code": STREAM_DECODE_FAILED,
                        "message": exc.stderr_text or str(exc),
                        "fatal": True,
                    }
                )
            return []
        except Exception:  # pragma: no cover - defensive
            log.exception("decoder shutdown failed")
            return []
        events: list[StreamEvent] = []
        for block in decoder.drain():
            events.extend(session.on_audio(block))
        return events

    def drive() -> None:
        """The session's owner thread: decode -> segment -> transcribe -> publish."""
        try:
            while True:
                if abort.is_set():
                    return
                for block in decoder.drain():
                    emit(session.on_audio(block))
                if not session.ended:
                    emit(session.poll())
                if session.ended:
                    emit(close_decoder(report=True))
                    return
                if stop.is_set():
                    emit(close_decoder(report=True))
                    emit(session.stop())
                    return
                time.sleep(_POLL_INTERVAL_S)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("stream worker failed")
            emit_frame(
                {
                    "type": "error",
                    "code": STREAM_INTERNAL,
                    "message": f"{type(exc).__name__}: {exc}",
                    "fatal": True,
                }
            )
        finally:
            with contextlib.suppress(Exception):
                decoder.close()
            loop.call_soon_threadsafe(frames.put_nowait, _END)

    async def receive_loop() -> None:
        while True:
            incoming = await websocket.receive()
            if incoming.get("type") == "websocket.disconnect":
                abort.set()
                return
            data = incoming.get("bytes")
            if data:
                if len(data) > settings.stream_max_frame_bytes:
                    emit_frame(
                        {
                            "type": "error",
                            "code": STREAM_BAD_REQUEST,
                            "message": _too_big(len(data), settings),
                            "fatal": True,
                        }
                    )
                    stop.set()
                    return
                await asyncio.to_thread(decoder.write, data)
                continue
            text = incoming.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                emit_frame(
                    {
                        "type": "error",
                        "code": STREAM_BAD_REQUEST,
                        "message": "control frames must be JSON",
                        "fatal": False,
                    }
                )
                continue
            kind = payload.get("type") if isinstance(payload, dict) else None
            if kind in {"stop", "close"}:
                stop.set()
                return
            emit_frame(
                {
                    "type": "error",
                    "code": STREAM_BAD_REQUEST,
                    "message": f"unknown control frame {kind!r}",
                    "fatal": False,
                }
            )

    async def send_loop() -> None:
        while True:
            frame = await frames.get()
            if frame is _END:
                return
            await websocket.send_json(frame)

    worker = threading.Thread(
        target=drive, daemon=True, name=f"stream-{session_id[:8]}"
    )
    worker.start()
    if first_chunk:
        decoder.write(first_chunk)

    send_task = asyncio.create_task(send_loop())
    receive_task = asyncio.create_task(receive_loop())
    try:
        done, _ = await asyncio.wait(
            {send_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if send_task not in done:
            # The client stopped (or went away) while the worker still had a
            # window to flush: give it the time to say its last word.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(send_task, timeout=_DRAIN_TIMEOUT_S)
    finally:
        abort.set()
        for task in (send_task, receive_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(send_task, receive_task, return_exceptions=True)
        worker.join(timeout=10.0)
        with contextlib.suppress(Exception):
            await websocket.close()
        log.info("stream session ended id=%s duration=%.1fs", session_id[:8], session.duration_s)


def _parse_start(message: Mapping[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    """The optional start frame, plus audio if the client sent it first."""
    data = message.get("bytes")
    if data:
        return {}, data
    text = message.get("text")
    if not text:
        raise DomainError(
            "STREAM_BAD_REQUEST", "the first frame must be a start frame or audio"
        )
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DomainError(
            "STREAM_BAD_REQUEST", "the start frame must be JSON"
        ) from exc
    if not isinstance(payload, dict) or payload.get("type") != "start":
        raise DomainError(
            "STREAM_BAD_REQUEST", 'the first frame must be {"type": "start"}'
        )
    return payload, None


def _too_big(size: int, settings: Settings) -> str:
    return (
        f"a frame of {size} bytes exceeds the {settings.stream_max_frame_bytes}-byte "
        "streaming frame limit; send smaller chunks"
    )


async def _fail(
    websocket: WebSocket, code: str, message: str, *, close_code: int = _WS_POLICY
) -> None:
    """Tell the client why, then close with a code that says who ended it."""
    with contextlib.suppress(Exception):
        await websocket.send_json(
            {"type": "error", "code": code, "message": message, "fatal": True}
        )
    with contextlib.suppress(Exception):
        await websocket.close(code=close_code)
