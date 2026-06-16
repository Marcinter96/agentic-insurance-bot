"""
Minimal ADK Live voice test — gemini-3.1-flash-live-preview (AI Studio).

Standalone mic <-> speaker loop using Google ADK's ``Runner.run_live()`` over
the AI Studio (Gemini Developer API) Live API. NO insurance_bot, NO GCS — just
a bare ADK ``LlmAgent`` driven live, to verify voice works end-to-end.

    mic     --(16kHz PCM)-->  LiveRequestQueue.send_realtime()  -->  Gemini Live
    speaker <--(24kHz PCM)--  runner.run_live() events          <--  Gemini Live

Unlike the raw genai ``session.receive()`` (which exhausts after each turn),
``runner.run_live()`` is a single long-lived event stream — the conversation
keeps going until you stop with Ctrl+C.

Setup (.env):
    GOOGLE_API_KEY=<your AI Studio key>      # from https://aistudio.google.com/apikey
    LIVE_MODEL=gemini-3.1-flash-live-preview
    SSL_CERT_FILE=/path/to/ca-bundle.pem     # only if behind a TLS proxy

Logging level is the LOG_LEVEL constant below (hardcoded INFO for now).

Press Ctrl+C to stop.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env so GOOGLE_API_KEY / LIVE_MODEL / SSL_CERT_FILE are available.
load_dotenv()

# Force the AI Studio (Gemini Developer API) backend, not Vertex.
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"

# If behind a TLS-inspecting proxy, make sure both vars point at the CA bundle
# (some HTTP stacks read SSL_CERT_FILE, others REQUESTS_CA_BUNDLE).
_ca = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
if _ca:
    os.environ.setdefault("SSL_CERT_FILE", _ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)

MODEL = os.getenv("LIVE_MODEL", "gemini-3.1-flash-live-preview")

# Logging level (NOT from env). Override on the CLI with --log-level.
LOG_LEVEL = "INFO"  # DEBUG | INFO | WARNING | ERROR

logger = logging.getLogger("live")  # configured in __main__ via _setup_logging()

APP_NAME = "aistudio_live_test"
USER_ID = "local_user"
SESSION_ID = "local_session"

INPUT_SAMPLE_RATE = 16000   # mic -> model
OUTPUT_SAMPLE_RATE = 24000  # model -> speaker
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 1600            # ~100ms at 16kHz


# ---------------------------------------------------------------------------
# Logging — rich handler with colour, levels, and markup
# ---------------------------------------------------------------------------

def _setup_logging(level: str) -> logging.Logger:
    try:
        from rich.logging import RichHandler
        from rich.console import Console

        handler = RichHandler(
            console=Console(stderr=True),
            rich_tracebacks=True,
            markup=True,
            show_path=(level == "DEBUG"),
            log_time_format="%H:%M:%S",
        )
        logging.basicConfig(level=level, format="%(message)s", handlers=[handler])
    except Exception:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-5s %(message)s",
            datefmt="%H:%M:%S",
        )

    log = logging.getLogger("live")
    noisy = logging.WARNING if level != "DEBUG" else logging.INFO
    for name in ("google_genai", "websockets", "httpx", "google_adk"):
        logging.getLogger(name).setLevel(noisy)
    return log


async def main() -> None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.error("[red]GOOGLE_API_KEY is not set.[/red] Add it to .env first.")
        sys.exit(1)

    try:
        import sounddevice as sd
    except Exception as exc:
        logger.error("sounddevice import failed (%s). Try: brew install portaudio && pip install sounddevice", exc)
        sys.exit(1)

    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.adk.agents.live_request_queue import LiveRequestQueue
    from google.genai import types

    # Bare ADK agent on the AI Studio Live model.
    agent = LlmAgent(
        name="live_test_agent",
        model=MODEL,
        instruction="You are a friendly assistant. Keep replies short and conversational.",
    )
    logger.info("[bold cyan]Model:[/bold cyan] %s [dim](ADK run_live, AI Studio)[/dim]", MODEL)

    runner = InMemoryRunner(app_name=APP_NAME, agent=agent)
    await runner.session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)

    live_request_queue = LiveRequestQueue()
    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    logger.debug("RunConfig: BIDI, AUDIO, transcription=ON | block=%d", BLOCKSIZE)

    loop = asyncio.get_running_loop()
    stats = {"mic_frames": 0, "mic_bytes": 0, "spk_chunks": 0, "spk_bytes": 0}

    # --- Microphone: capture -> queue -----------------------------------
    def mic_callback(indata, frames, time_info, status):
        if status:
            logger.warning("[yellow][mic][/yellow] %s", status)
        data = bytes(indata)
        stats["mic_frames"] += 1
        stats["mic_bytes"] += len(data)
        blob = types.Blob(data=data, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}")
        try:
            loop.call_soon_threadsafe(live_request_queue.send_realtime, blob)
        except RuntimeError:
            pass
        if logger.isEnabledFor(logging.DEBUG) and stats["mic_frames"] % 20 == 0:
            logger.debug("[mic] sent %d frames (%.1f KB)", stats["mic_frames"], stats["mic_bytes"] / 1024)

    mic = sd.RawInputStream(
        samplerate=INPUT_SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
        blocksize=BLOCKSIZE, callback=mic_callback,
    )
    speaker = sd.RawOutputStream(samplerate=OUTPUT_SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE)
    mic.start()
    speaker.start()
    logger.info("[green]Live session starting.[/green] Speak into your microphone. [dim]Ctrl+C to stop.[/dim]")

    try:
        # run_live() is a single long-lived event stream for the whole session.
        #
        # Fields ADK 2.2.0 actually exposes on each Event (others like VAD /
        # voice_activity and generation_complete are dropped by the framework):
        #   partial               -> prefix / streaming chunk (text & transcription)
        #   input/output_transcription (.text, .finished)
        #   interrupted           -> user barge-in
        #   turn_complete + turn_complete_reason
        #   content.parts[].inline_data -> audio bytes (no partial flag)
        #   go_away               -> server closing soon
        #   usage_metadata        -> token usage
        #
        # Example events seen during ONE bot turn ("Hello, how can I help you today?"),
        # captured live (author = agent name for the bot, "user" for your speech):
        #
        #   # user speech transcription (arrives in chunks, then a final one):
        #   Event(author='user', partial=True,
        #         input_transcription=Transcription(text='can you', finished=False))
        #   Event(author='user', partial=False,
        #         input_transcription=Transcription(text='can you hear me?', finished=True))
        #
        #   # bot audio chunks — partial is None, no transcription on these:
        #   Event(author='t', partial=None,
        #         content=Content(parts=[Part(inline_data=Blob(
        #             mime_type='audio/pcm;rate=24000', data=b'...9600 bytes...'))]))
        #
        #   # bot speech transcription chunks (partial=True), then final (finished=True):
        #   Event(author='t', partial=True,
        #         output_transcription=Transcription(text='Hello, how', finished=False))
        #   Event(author='t', partial=True,
        #         output_transcription=Transcription(text=' can I', finished=False))
        #   Event(author='t', partial=False,
        #         output_transcription=Transcription(
        #             text='Hello, how can I help you today?', finished=True))
        #
        #   # end of turn (no content; reason is usually None, e.g. NEED_MORE_INPUT):
        #   Event(author='t', turn_complete=True, turn_complete_reason=None)
        #
        #   # barge-in (its own content-less event, or rides on turn_complete):
        #   Event(author='t', interrupted=True)
        #
        #   # server about to close the socket:
        #   Event(go_away=LiveServerGoAway(time_left=...))
        async for event in runner.run_live(
            user_id=USER_ID,
            session_id=SESSION_ID,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            prefix = "[dim](partial)[/dim] " if getattr(event, "partial", False) else ""

            in_tx = getattr(event, "input_transcription", None)
            if in_tx and getattr(in_tx, "text", None):
                done = "[dim]✓[/dim]" if getattr(in_tx, "finished", False) else ""
                logger.info("[bold blue][customer][/bold blue] %s%s %s", prefix, in_tx.text.strip(), done)
            out_tx = getattr(event, "output_transcription", None)
            if out_tx and getattr(out_tx, "text", None):
                done = "[dim]✓[/dim]" if getattr(out_tx, "finished", False) else ""
                logger.info("[bold green][bot][/bold green] %s%s %s", prefix, out_tx.text.strip(), done)

            if getattr(event, "interrupted", False):
                logger.info("[yellow][interrupted][/yellow] user barged in")
                continue

            content = getattr(event, "content", None)
            if content and getattr(content, "parts", None):
                for p in content.parts:
                    inline = getattr(p, "inline_data", None)
                    if inline and inline.data:
                        stats["spk_chunks"] += 1
                        stats["spk_bytes"] += len(inline.data)
                        speaker.write(inline.data)
                    fc = getattr(p, "function_call", None)
                    if fc:
                        logger.info("[magenta][tool-call][/magenta] %s args=%s", fc.name, dict(fc.args or {}))
                    fr = getattr(p, "function_response", None)
                    if fr:
                        logger.info("[magenta][tool-result][/magenta] %s", fr.name)

            if getattr(event, "turn_complete", False):
                reason = getattr(event, "turn_complete_reason", None)
                reason_str = f" reason={reason}" if reason else ""
                logger.info("[cyan][turn-complete][/cyan] spoke %.1f KB%s",
                            stats["spk_bytes"] / 1024, reason_str)
                stats["spk_bytes"] = 0
                stats["spk_chunks"] = 0

            if getattr(event, "go_away", None):
                logger.warning("[yellow][go-away][/yellow] server closing soon: %s", event.go_away)

            usage = getattr(event, "usage_metadata", None)
            if usage:
                logger.debug("[dim][usage][/dim] %s", usage)

            if getattr(event, "error_code", None):
                logger.error("[red][error %s][/red] %s", event.error_code, getattr(event, "error_message", ""))
    except asyncio.CancelledError:
        pass
    finally:
        mic.stop(); mic.close()
        speaker.stop(); speaker.close()
        live_request_queue.close()
        logger.info("[dim]Bye.[/dim]")


if __name__ == "__main__":
    logger = _setup_logging(LOG_LEVEL)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
