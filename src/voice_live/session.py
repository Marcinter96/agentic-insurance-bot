"""The live voice session: wires audio I/O to ADK's run_live() event stream.

``run_live()`` is a single long-lived event stream for the whole session
(unlike raw genai ``session.receive()``, which exhausts after each turn), so
the conversation keeps going until the caller cancels.
"""

from __future__ import annotations

import asyncio

from google.genai import types

from voice_live import config
from voice_live.agent import LiveAgentBundle
from voice_live.audio import AudioIO
from voice_live.event_dump import EventRecorder
from voice_live.logging_setup import get_logger

logger = get_logger(__name__)


def _format_usage(usage) -> str:
    """Compact one-line token usage: only non-empty fields + per-modality split."""
    parts: list[str] = []
    prompt = getattr(usage, "prompt_token_count", None)
    out = getattr(usage, "candidates_token_count", None)
    think = getattr(usage, "thoughts_token_count", None)
    cached = getattr(usage, "cached_content_token_count", None)
    total = getattr(usage, "total_token_count", None)

    if prompt is not None:
        parts.append(f"prompt={prompt}")
    if out is not None:
        parts.append(f"out={out}")
    if think:  # only show reasoning tokens when the model actually used them
        parts.append(f"think={think}")
    if cached:
        parts.append(f"cached={cached}")
    if total is not None:
        parts.append(f"total={total}")

    def _modalities(details) -> str:
        if not details:
            return ""
        bits = [f"{d.modality.value.lower()}={d.token_count}" for d in details]
        return "(" + " ".join(bits) + ")"

    in_mods = _modalities(getattr(usage, "prompt_tokens_details", None))
    if in_mods:
        parts.append(f"in{in_mods}")
    out_mods = _modalities(getattr(usage, "candidates_tokens_details", None))
    if out_mods:
        parts.append(f"out{out_mods}")

    return "  ".join(parts) if parts else "n/a"


class LiveVoiceSession:
    """Runs one continuous mic <-> speaker live session."""

    def __init__(self, bundle: LiveAgentBundle) -> None:
        self._bundle = bundle
        self._spoken_bytes = 0
        self._audio: AudioIO | None = None
        self._recorder = EventRecorder()

    async def run(self) -> None:
        """Open audio devices and consume the run_live() event stream."""
        loop = asyncio.get_running_loop()
        queue = self._bundle.live_request_queue

        def _on_mic_frame(data: bytes) -> None:
            blob = types.Blob(data=data, mime_type=f"audio/pcm;rate={config.INPUT_SAMPLE_RATE}")
            try:
                loop.call_soon_threadsafe(queue.send_realtime, blob)
            except RuntimeError:
                pass  # loop closed during shutdown

        self._audio = AudioIO(on_mic_frame=_on_mic_frame)
        self._audio.start()
        self._recorder.open()
        logger.info(
            "[bold green]READY[/bold green]     Speak into your microphone.  [dim]Ctrl+C to stop.[/dim]"
        )

        try:
            async for event in self._bundle.runner.run_live(
                user_id=self._bundle.settings.user_id,
                session_id=self._bundle.settings.session_id,
                live_request_queue=queue,
                run_config=self._bundle.run_config,
            ):
                self._recorder.record(event)
                self._handle_event(event)
        except asyncio.CancelledError:
            pass
        finally:
            self._recorder.close()
            self._audio.close()
            queue.close()
            logger.info("[dim]STOP      session closed. Bye.[/dim]")

    # -- event handling -----------------------------------------------------

    def _handle_event(self, event) -> None:
        """Log and act on one ADK Event from run_live().

        Fields ADK 2.2.0 exposes (VAD / voice_activity and generation_complete
        are dropped by the framework and are NOT reachable here):
            partial               -> prefix / streaming chunk (text & transcription)
            input/output_transcription (.text, .finished)
            interrupted           -> user barge-in
            turn_complete + turn_complete_reason
            content.parts[].inline_data -> audio bytes (no partial flag)
            go_away               -> server closing soon
            usage_metadata        -> token usage

        Example events during ONE bot turn ("Hello, how can I help you today?"),
        captured live (author = agent name for the bot, "user" for speech):

            # user speech transcription (chunks, then a final one):
            Event(author='user', partial=True,
                  input_transcription=Transcription(text='can you', finished=False))
            Event(author='user', partial=False,
                  input_transcription=Transcription(text='can you hear me?', finished=True))

            # bot audio chunks — partial is None, no transcription on these:
            Event(author='voice_live_agent', partial=None,
                  content=Content(parts=[Part(inline_data=Blob(
                      mime_type='audio/pcm;rate=24000', data=b'...9600 bytes...'))]))

            # bot speech transcription chunks (partial=True), then final (finished=True):
            Event(author='voice_live_agent', partial=True,
                  output_transcription=Transcription(text='Hello, how', finished=False))
            Event(author='voice_live_agent', partial=False,
                  output_transcription=Transcription(
                      text='Hello, how can I help you today?', finished=True))

            # end of turn (no content; reason usually None, e.g. NEED_MORE_INPUT):
            Event(author='voice_live_agent', turn_complete=True, turn_complete_reason=None)

            # barge-in (own content-less event, or rides on turn_complete):
            Event(author='voice_live_agent', interrupted=True)

            # server about to close the socket:
            Event(go_away=LiveServerGoAway(time_left=...))
        """
        # Tag describing the event category so log lines are self-explanatory.
        # Markup wraps the WHOLE visible label, so the text always shows.
        partial = bool(getattr(event, "partial", False))
        flag = " [dim](partial)[/dim]" if partial else " [dim](final)[/dim]"

        in_tx = getattr(event, "input_transcription", None)
        if in_tx and getattr(in_tx, "text", None):
            logger.info("[bold blue]CUSTOMER[/bold blue]%s  %s", flag, in_tx.text.strip())

        out_tx = getattr(event, "output_transcription", None)
        if out_tx and getattr(out_tx, "text", None):
            logger.info("[bold green]BOT     [/bold green]%s  %s", flag, out_tx.text.strip())

        if getattr(event, "interrupted", False):
            # Barge-in: stop the bot immediately by dropping all queued audio.
            dropped = self._audio.flush() if self._audio else 0
            logger.info(
                "[bold yellow]INTERRUPT[/bold yellow]  user barged in — flushed %.1f KB unplayed",
                dropped / 1024,
            )
            self._spoken_bytes = 0
            return

        content = getattr(event, "content", None)
        if content and getattr(content, "parts", None):
            for part in content.parts:
                # Reasoning / "thinking" arrives as a text part flagged thought=True.
                if getattr(part, "thought", False) and getattr(part, "text", None):
                    logger.info("[bold white]THINK   [/bold white]%s  %s", flag, part.text.strip())
                    continue
                inline = getattr(part, "inline_data", None)
                if inline and inline.data and self._audio:
                    self._spoken_bytes += len(inline.data)
                    self._audio.play(inline.data)
                fc = getattr(part, "function_call", None)
                if fc:
                    logger.info("[bold magenta]TOOL-CALL[/bold magenta]  %s args=%s", fc.name, dict(fc.args or {}))
                fr = getattr(part, "function_response", None)
                if fr:
                    logger.info("[bold magenta]TOOL-RESP[/bold magenta]  %s", fr.name)

        if getattr(event, "turn_complete", False):
            reason = getattr(event, "turn_complete_reason", None)
            reason_str = f" reason={reason}" if reason else ""
            logger.info("[bold cyan]TURN    [/bold cyan]  complete — spoke %.1f KB%s",
                        self._spoken_bytes / 1024, reason_str)
            self._spoken_bytes = 0

        if getattr(event, "go_away", None):
            logger.warning("[bold yellow]GO-AWAY [/bold yellow]  server closing soon: %s", event.go_away)

        usage = getattr(event, "usage_metadata", None)
        if usage:
            logger.info("[dim]USAGE     %s[/dim]", _format_usage(usage))

        if getattr(event, "error_code", None):
            logger.error("[bold red]ERROR[/bold red] %s — %s", event.error_code, getattr(event, "error_message", ""))
