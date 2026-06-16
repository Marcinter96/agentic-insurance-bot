"""Microphone capture and speaker playback via sounddevice.

``AudioIO`` opens a 16 kHz mic input and a 24 kHz speaker output, forwarding
captured mic frames to a callback (which feeds the live request queue) and
exposing ``play()`` for model audio chunks.
"""

from __future__ import annotations

import threading
from typing import Callable

from voice_live import config
from voice_live.logging_setup import get_logger

logger = get_logger(__name__)


class AudioIO:
    """Manages the microphone input and speaker output streams.

    Speaker playback is driven by a sounddevice *output callback* pulling from
    an in-memory PCM buffer. This keeps ``play()`` non-blocking and, crucially,
    lets ``flush()`` drop all pending audio the instant the model is
    interrupted (barge-in) so the bot stops talking immediately.
    """

    def __init__(self, on_mic_frame: Callable[[bytes], None]) -> None:
        """Args:
        on_mic_frame: Called (from the audio thread) with each raw PCM frame.
        """
        self._on_mic_frame = on_mic_frame
        self._mic = None
        self._speaker = None
        self.mic_frames = 0
        self.mic_bytes = 0

        # Playback buffer (raw 24 kHz int16 PCM bytes), guarded by a lock since
        # the sounddevice output callback runs on its own thread.
        self._buf = bytearray()
        self._buf_lock = threading.Lock()
        self._bytes_per_frame = 2 * config.CHANNELS  # int16 mono

    def start(self) -> None:
        """Open and start the mic + speaker streams."""
        import sounddevice as sd

        def _mic_callback(indata, _frames, _time, status):
            if status:
                logger.warning("[yellow][mic][/yellow] %s", status)
            data = bytes(indata)
            self.mic_frames += 1
            self.mic_bytes += len(data)
            self._on_mic_frame(data)
            if self.mic_frames % 20 == 0:
                logger.info("[mic] sent %d frames (%.1f KB)", self.mic_frames, self.mic_bytes / 1024)

        def _speaker_callback(outdata, frames, _time, status):
            if status:
                logger.warning("[yellow][speaker][/yellow] %s", status)
            want = frames * self._bytes_per_frame
            with self._buf_lock:
                take = min(want, len(self._buf))
                chunk = bytes(self._buf[:take])
                del self._buf[:take]
            if take < want:
                chunk += b"\x00" * (want - take)  # silence-fill underruns
            outdata[:] = chunk

        self._mic = sd.RawInputStream(
            samplerate=config.INPUT_SAMPLE_RATE,
            channels=config.CHANNELS,
            dtype=config.DTYPE,
            blocksize=config.BLOCKSIZE,
            callback=_mic_callback,
        )
        self._speaker = sd.RawOutputStream(
            samplerate=config.OUTPUT_SAMPLE_RATE,
            channels=config.CHANNELS,
            dtype=config.DTYPE,
            callback=_speaker_callback,
        )
        self._mic.start()
        self._speaker.start()
        logger.info("mic + speaker streams started")

    def play(self, pcm: bytes) -> None:
        """Queue a chunk of 24 kHz PCM for playback (non-blocking)."""
        with self._buf_lock:
            self._buf.extend(pcm)

    def flush(self) -> int:
        """Drop all pending playback audio (used on barge-in / interruption).

        Returns:
            Number of unplayed PCM bytes discarded.
        """
        with self._buf_lock:
            dropped = len(self._buf)
            self._buf.clear()
        return dropped

    def pending_bytes(self) -> int:
        """Bytes still queued for playback."""
        with self._buf_lock:
            return len(self._buf)

    def close(self) -> None:
        """Stop and close both streams."""
        self.flush()
        for stream in (self._mic, self._speaker):
            if stream:
                try:
                    stream.stop()
                    stream.close()
                except Exception:  # pragma: no cover - best-effort cleanup
                    pass


def check_sounddevice() -> None:
    """Import sounddevice early with a friendly error if missing."""
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            f"sounddevice import failed ({exc}). "
            "Try: brew install portaudio && pip install sounddevice"
        ) from exc
