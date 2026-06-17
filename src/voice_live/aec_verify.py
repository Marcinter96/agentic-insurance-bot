"""Closed-loop AEC verification against the REAL speaker+mic.

Plays a bot-like signal out the speaker via AudioIO (so the AEC reference is
fed exactly as in production) while the real mic captures the room (which
hears the speaker echo). Compares the energy of the echo-cancelled mic stream
(what the model receives) against a raw-mic baseline to report the achieved
echo reduction in dB.

Run (stay quiet, speaker audible):
    python -m voice_live.aec_verify
"""

from __future__ import annotations

import time

import numpy as np

from voice_live import config
from voice_live.audio import AudioIO


def _tone_24k(seconds: float) -> bytes:
    """A bot-like 24 kHz PCM signal (two tones) to play out the speaker."""
    rate = config.OUTPUT_SAMPLE_RATE
    t = np.arange(int(rate * seconds)) / rate
    sig = 0.4 * np.sin(2 * np.pi * 330 * t) + 0.2 * np.sin(2 * np.pi * 1500 * t)
    return (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()


def _rms(frames: list[bytes]) -> float:
    if not frames:
        return 0.0
    x = np.frombuffer(b"".join(frames), dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(x**2))) if x.size else 0.0


def _run(aec_on: bool, seconds: float = 4.0) -> float:
    config.AEC_ENABLED = aec_on
    captured: list[bytes] = []
    audio = AudioIO(on_mic_frame=captured.append)  # what the MODEL would receive
    audio.start()
    audio.play(_tone_24k(seconds))
    time.sleep(seconds + 0.5)
    audio.close()
    # skip the first second (filter convergence) for the AEC measurement
    skip = int(config.INPUT_SAMPLE_RATE * 1.0) * 2  # bytes
    joined = b"".join(captured)
    return _rms([joined[skip:]])


def main() -> None:
    print("AEC verification — keep quiet, speaker must be audible.\n")
    print("1/2: BASELINE (AEC off) — measuring echo the mic hears...")
    base = _run(aec_on=False)
    time.sleep(0.5)
    print("2/2: AEC ON — measuring residual echo after cancellation...")
    with_aec = _run(aec_on=True)

    print(f"\nmic RMS, AEC off : {base:.1f}")
    print(f"mic RMS, AEC on  : {with_aec:.1f}")
    if with_aec > 0:
        print(f"echo reduction   : {20 * np.log10(base / (with_aec + 1e-9)):.1f} dB")
    if base < 50:
        print("WARNING: baseline echo very low — speaker may be muted/too quiet.")


if __name__ == "__main__":
    main()
