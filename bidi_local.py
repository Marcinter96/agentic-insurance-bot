"""
Local bidirectional (bidi) live voice run for the insurance bot.

Connects the machine's microphone and speaker directly to the ADK
`root_agent` via the Gemini Live API:

    mic  --(16kHz PCM)-->  LiveRequestQueue.send_realtime()  -->  Gemini Live
    speaker  <--(24kHz PCM)--  runner.run_live() audio events  <--  Gemini Live

Logging is intentionally verbose at INFO level so the full bidi loop can be
debugged (connection, audio frame sizes, transcripts, tool calls, turn
boundaries, interruptions). Once the flow is verified, flip LOG_LEVEL to DEBUG
for even more detail, or raise it to WARNING for quiet operation.

Run:
    python bidi_local.py

Press Ctrl+C to stop.
"""

import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv

# Load .env before importing the agent (model/env selection happens at import).
load_dotenv()

# Audio format constants required by the Gemini Live API.
INPUT_SAMPLE_RATE = 16000   # mic -> model: 16 kHz, 16-bit, mono PCM
OUTPUT_SAMPLE_RATE = 24000  # model -> speaker: 24 kHz, 16-bit, mono PCM
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 1600            # 100ms at 16kHz; frames per mic callback

APP_NAME = "insurance_bot_bidi"
USER_ID = "local_user"
SESSION_ID = "local_session"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bidi")

# Quiet down noisy third-party loggers unless we're explicitly debugging.
if LOG_LEVEL != "DEBUG":
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _check_imports():
    """Import heavy deps with friendly errors if missing."""
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:  # pragma: no cover
        logger.error(
            "sounddevice import failed (%s). Install with: pip install sounddevice "
            "(and the PortAudio system lib: `brew install portaudio` on macOS).",
            exc,
        )
        sys.exit(1)


async def main() -> None:
    _check_imports()

    import sounddevice as sd
    from google.adk.runners import InMemoryRunner
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.genai import types

    # Import the agent and point it at the native-audio Live model.
    import agent as agent_module

    root_agent = agent_module.root_agent
    bidi_model = agent_module.BIDI_MODEL
    root_agent.model = bidi_model
    logger.info("Using bidi model: %s", bidi_model)
    logger.info(
        "Vertex AI backend: %s | project=%s | location=%s",
        os.getenv("GOOGLE_GENAI_USE_VERTEXAI"),
        os.getenv("GOOGLE_CLOUD_PROJECT"),
        os.getenv("GOOGLE_CLOUD_LOCATION"),
    )

    # ----- ADK runner + session -------------------------------------------
    runner = InMemoryRunner(app_name=APP_NAME, agent=root_agent)
    await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID
    )
    logger.info("Created session %s for user %s", SESSION_ID, USER_ID)

    live_request_queue = LiveRequestQueueFactory()

    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    logger.info(
        "RunConfig: mode=BIDI, modalities=AUDIO, input/output transcription=ON"
    )

    loop = asyncio.get_running_loop()

    # ----- Microphone: capture -> queue -----------------------------------
    mic_frames = 0
    mic_bytes = 0
    running = True  # cleared on shutdown so the mic callback stops scheduling

    def mic_callback(indata, frames, time_info, status):
        nonlocal mic_frames, mic_bytes
        if not running or loop.is_closed():
            return
        if status:
            logger.warning("[mic] stream status: %s", status)
        data = bytes(indata)
        mic_frames += 1
        mic_bytes += len(data)
        # Send raw PCM to the model. Thread-safe scheduling onto the loop.
        try:
            loop.call_soon_threadsafe(
                live_request_queue.send_realtime,
                types.Blob(data=data, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}"),
            )
        except RuntimeError:
            return  # loop closed during shutdown
        if mic_frames % 20 == 0:  # ~ every 2s at 100ms blocks
            logger.info(
                "[mic] sent %d frames (%.1f KB total) to model",
                mic_frames, mic_bytes / 1024,
            )

    mic_stream = sd.RawInputStream(
        samplerate=INPUT_SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=BLOCKSIZE,
        callback=mic_callback,
    )

    # ----- Speaker: model audio events -> playback ------------------------
    speaker_stream = sd.RawOutputStream(
        samplerate=OUTPUT_SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
    )

    async def downstream():
        """Consume run_live() events and play audio. Logs EVERY event."""
        out_chunks = 0
        out_bytes = 0
        event_no = 0
        async for event in runner.run_live(
            user_id=USER_ID,
            session_id=SESSION_ID,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            event_no += 1

            # ---- 1) Always log a one-line summary of the raw event --------
            author = getattr(event, "author", "?")
            flags = []
            for attr in (
                "partial", "turn_complete", "interrupted",
                "error_code", "error_message",
            ):
                val = getattr(event, attr, None)
                if val:
                    flags.append(f"{attr}={val}")
            # Describe content parts compactly.
            content = getattr(event, "content", None)
            part_kinds = []
            if content and getattr(content, "parts", None):
                for p in content.parts:
                    if getattr(p, "inline_data", None) and p.inline_data.data:
                        part_kinds.append(f"audio({len(p.inline_data.data)}B)")
                    elif getattr(p, "text", None):
                        part_kinds.append("text")
                    elif getattr(p, "function_call", None):
                        part_kinds.append(f"call:{p.function_call.name}")
                    elif getattr(p, "function_response", None):
                        part_kinds.append(f"result:{p.function_response.name}")
                    else:
                        part_kinds.append("other")
            # Extra signals some events carry.
            for attr in ("usage_metadata", "grounding_metadata", "actions",
                         "long_running_tool_ids", "custom_metadata",
                         "input_transcription", "output_transcription"):
                if getattr(event, attr, None):
                    flags.append(attr)
            role = getattr(content, "role", None) if content else None
            logger.info(
                "[event #%d] author=%s role=%s parts=[%s] %s",
                event_no, author, role,
                ", ".join(part_kinds) if part_kinds else "-",
                " ".join(flags) if flags else "",
            )

            # ---- 1.5) TRANSCRIPTS (dedicated fields, not in content.parts) --
            in_tx = getattr(event, "input_transcription", None)
            if in_tx and getattr(in_tx, "text", None):
                logger.info("[TRANSCRIPT user] %s", in_tx.text.strip())
            out_tx = getattr(event, "output_transcription", None)
            if out_tx and getattr(out_tx, "text", None):
                logger.info("[TRANSCRIPT agent] %s", out_tx.text.strip())

            # ---- 2) Interruption (user barged in) -------------------------
            if getattr(event, "interrupted", False):
                logger.info("[event #%d] INTERRUPTED by user - flushing speaker", event_no)
                continue

            # ---- 3) Detailed per-part handling ----------------------------
            if content and getattr(content, "parts", None):
                for part in content.parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        out_chunks += 1
                        out_bytes += len(inline.data)
                        speaker_stream.write(inline.data)
                        if out_chunks % 20 == 0:
                            logger.info(
                                "[speaker] played %d chunks (%.1f KB) from model",
                                out_chunks, out_bytes / 1024,
                            )
                    text = getattr(part, "text", None)
                    if text:
                        logger.info("[transcript:%s] %s", role, text.strip())
                    fc = getattr(part, "function_call", None)
                    if fc:
                        logger.info("[tool-call] %s args=%s", fc.name, dict(fc.args or {}))
                    fr = getattr(part, "function_response", None)
                    if fr:
                        logger.info("[tool-result] %s -> %s", fr.name, fr.response)

            # ---- 4) Other notable signals ---------------------------------
            if getattr(event, "turn_complete", False):
                logger.info("[event #%d] turn complete", event_no)
            usage = getattr(event, "usage_metadata", None)
            if usage:
                logger.info("[usage] %s", usage)
            if getattr(event, "error_code", None):
                logger.error(
                    "[event #%d] error %s: %s",
                    event_no, event.error_code, getattr(event, "error_message", ""),
                )

            # ---- 5) DEBUG: dump the full event object ---------------------
            logger.debug("[event #%d raw] %r", event_no, event)

    # ----- Start everything ------------------------------------------------
    logger.info("Starting mic + speaker streams. Speak into your microphone.")
    logger.info("Press Ctrl+C to stop.")
    mic_stream.start()
    speaker_stream.start()

    downstream_task = asyncio.create_task(downstream())

    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Stop requested.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down...")
        running = False
        # Stop audio devices first so callbacks stop firing into the loop.
        mic_stream.stop(); mic_stream.close()
        speaker_stream.stop(); speaker_stream.close()
        downstream_task.cancel()
        try:
            await downstream_task
        except asyncio.CancelledError:
            pass
        live_request_queue.close()
        logger.info("Bye.")


def LiveRequestQueueFactory():
    """Construct a LiveRequestQueue (kept in a helper for a single import site)."""
    from google.adk.agents.live_request_queue import LiveRequestQueue

    q = LiveRequestQueue()
    logging.getLogger("bidi").info("LiveRequestQueue created")
    return q


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
