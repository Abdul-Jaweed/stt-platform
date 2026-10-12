"""The Deepgram real-time adapter, against a socket that speaks its protocol.

No paid call: a local vendor stand-in accepts the connection, checks the
auth header and the query parameters, answers with the same JSON shapes
Deepgram documents, and expects ``CloseStream`` on the way out.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from stt_platform.core.config import Settings
from stt_platform.providers.managed.deepgram import (
    DeepgramProvider,
    stream_event_from_message,
)
from stt_platform.providers.managed.deepgram import (
    _stream_url as stream_url,
)
from stt_platform.services.streaming import (
    ManagedStreamConfig,
    as_managed_stream_provider,
)

# ------------------------------------------------------------------ contract


def test_only_a_provider_that_can_open_a_stream_is_one():
    assert as_managed_stream_provider(object()) is None
    assert as_managed_stream_provider(DeepgramProvider(Settings(_env_file=None, deepgram_api_key="k"))) is not None


def test_the_socket_url_carries_the_format_the_decoder_produces():
    url = stream_url("https://api.deepgram.com", ManagedStreamConfig(), "nova-3")

    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    assert "encoding=linear16" in url and "sample_rate=16000" in url
    assert "channels=1" in url and "model=nova-3" in url
    assert "interim_results=true" in url


# ------------------------------------------------------------------ translation


def test_metadata_and_endpointing_messages_carry_no_transcript():
    assert stream_event_from_message({"type": "Metadata"}) is None
    assert stream_event_from_message({"type": "UtteranceEnd"}) is None
    assert stream_event_from_message({"type": "SpeechStarted"}) is None


def test_an_interim_result_is_a_partial_with_no_segments():
    event = stream_event_from_message(
        {
            "type": "Results",
            "is_final": False,
            "start": 0.5,
            "duration": 1.5,
            "channel": {"alternatives": [{"transcript": "hello wor"}]},
        }
    )
    assert event is not None and event.type == "partial"
    assert event.payload["text"] == "hello wor"
    assert event.payload["is_final"] is False
    assert event.payload["segments"] == []
    assert event.payload["window"] == {"start_s": 0.5, "end_s": 2.0}


def test_a_final_result_is_a_final_with_word_timings():
    event = stream_event_from_message(
        {
            "type": "Results",
            "is_final": True,
            "start": 0.0,
            "duration": 1.2,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "hello world",
                        "words": [
                            {"word": "hello", "punctuated_word": "Hello", "start": 0.1, "end": 0.4, "confidence": 0.98},
                            {"word": "world", "punctuated_word": "world.", "start": 0.4, "end": 0.9, "confidence": 0.91},
                        ],
                    }
                ]
            },
        }
    )
    assert event is not None and event.type == "final"
    assert event.payload["text"] == "hello world"
    segment = event.payload["segments"][0]
    assert segment["start"] == 0.1 and segment["end"] == 0.9
    assert [w["text"] for w in segment["words"]] == ["Hello", "world."]
    assert segment["words"][0]["confidence"] == 0.98


def test_a_degenerate_word_is_dropped_never_clamped():
    event = stream_event_from_message(
        {
            "type": "Results",
            "is_final": True,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "a b",
                        "words": [
                            {"word": "a", "start": 0.3, "end": 0.3, "confidence": 0.5},  # zero length
                            {"word": "b", "start": 0.4, "end": 0.8, "confidence": 0.5},
                        ],
                    }
                ]
            },
        }
    )
    words = event.payload["segments"][0]["words"]
    assert [w["text"] for w in words] == ["b"]


def test_a_vendor_error_becomes_an_error_event_not_a_silent_stop():
    event = stream_event_from_message({"type": "Error", "description": "quota exceeded"})
    assert event is not None and event.type == "error"
    assert event.payload["code"] == "PROVIDER_STREAM_ERROR"
    assert "quota exceeded" in event.payload["message"]


def test_an_unknown_message_type_is_loud_about_it():
    event = stream_event_from_message({"type": "WhoKnows"})
    assert event is not None and event.type == "error"
    assert "WhoKnows" in event.payload["message"]


# ------------------------------------------------------------------ the socket


def test_a_session_sends_pcm_and_reports_what_the_vendor_said():
    seen: dict[str, object] = {}

    async def vendor(websocket):
        seen["headers"] = dict(websocket.request.headers)
        seen["path"] = websocket.request.path
        async for frame in websocket:
            if isinstance(frame, bytes):
                seen["audio"] = seen.get("audio", b"") + frame
                await websocket.send(json.dumps({
                    "type": "Results", "is_final": False, "start": 0.0, "duration": 0.2,
                    "channel": {"alternatives": [{"transcript": "hel"}]},
                }))
                await websocket.send(json.dumps({
                    "type": "Results", "is_final": True, "start": 0.0, "duration": 0.4,
                    "channel": {"alternatives": [{"transcript": "hello", "words": [
                        {"word": "hello", "start": 0.05, "end": 0.4, "confidence": 0.9},
                    ]}]},
                }))
            else:
                seen["close_frame"] = json.loads(frame)
                await websocket.close()
                return

    async def run() -> tuple[list[dict], Settings]:
        async with websockets.serve(vendor, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            settings = Settings(
                _env_file=None,
                deepgram_api_key="test-key",
                deepgram_base_url=f"http://127.0.0.1:{port}",
            )
            provider = DeepgramProvider(settings)
            assert as_managed_stream_provider(provider) is not None
            events: list[dict] = []
            async with provider.open_stream(ManagedStreamConfig(model="nova-3")) as stream:
                # Sending and listening happen at the same time -- exactly how
                # the route drives a session. Awaiting the events first would
                # deadlock against a vendor that waits for more audio.
                async def collect() -> None:
                    async for event in stream.events():
                        events.append(event.to_wire())

                listener = asyncio.create_task(collect())
                await stream.send(b"\x00\x01" * 160)
                await asyncio.sleep(0.2)
                await stream.close()
                await asyncio.wait_for(listener, timeout=5)
            return events, settings

    events = asyncio.run(run())[0]

    assert [event["type"] for event in events] == ["partial", "final"]
    assert events[1]["text"] == "hello"
    assert events[1]["segments"][0]["words"][0]["start"] == 0.05
    # websockets lower-cases header names on the wire side of the handshake.
    headers = {str(k).lower(): v for k, v in dict(seen["headers"]).items()}  # type: ignore[arg-type]
    assert headers["authorization"] == "Token test-key"
    assert "model=nova-3" in str(seen["path"])
    assert seen["audio"] == b"\x00\x01" * 160
    assert seen["close_frame"] == {"type": "CloseStream"}


def test_sending_after_close_is_an_error_not_a_silent_no_op():
    async def run():
        provider = DeepgramProvider(
            Settings(_env_file=None, deepgram_api_key="k", deepgram_base_url="http://127.0.0.1:1")
        )
        session = provider.open_stream(ManagedStreamConfig())
        return session

    session = asyncio.run(run())
    with pytest.raises(Exception):
        asyncio.run(session.send(b"x"))
