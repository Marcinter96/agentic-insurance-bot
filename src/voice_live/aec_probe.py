"""Measure the speaker->mic acoustic round-trip delay.

Plays a short windowed linear chirp out the default speaker while recording
the default mic, then cross-correlates the recording against the emitted
signal to estimate the delay (samples + ms). This delay is what the echo
canceller must use to align its reference signal.

Run:
    python -m voice_live.aec_probe

Keep quiet during the ~1s measurement and make sure the speaker is audible.
A stable result across repeated runs means AEC alignment will be reliable.
"""

from __future__ import annotations

import numpy as np

from voice_live import config

RATE = config.INPUT_SAMPLE_RATE   # measure at the mic rate (16 kHz)
DUR = 0.20                        # chirp length (s)
SILENCE_PRE = 0.20                # lead-in silence
SILENCE_POST = 0.50               # trailing silence so the echo is captured
F0, F1 = 800.0, 4000.0            # chirp sweep range (Hz)
REPEATS = 5


def make_chirp() -> np.ndarray:
    """A Hann-windowed linear chirp (no clicks at the edges)."""
    t = np.linspace(0, DUR, int(RATE * DUR), endpoint=False)
    chirp = np.sin(2 * np.pi * (F0 * t + (F1 - F0) / (2 * DUR) * t**2))
    chirp *= np.hanning(len(chirp))
    return chirp.astype(np.float32)


def measure_once() -> tuple[int, float, float]:
    """Play the chirp, record, and return (delay_samples, delay_ms, mic_rms)."""
    import sounddevice as sd

    chirp = make_chirp()
    pre = np.zeros(int(RATE * SILENCE_PRE), dtype=np.float32)
    post = np.zeros(int(RATE * SILENCE_POST), dtype=np.float32)
    playback = np.concatenate([pre, chirp, post])

    recorded = sd.playrec(
        playback.reshape(-1, 1), samplerate=RATE, channels=1, dtype="float32"
    )
    sd.wait()
    rec = recorded[:, 0]

    rec_n = rec - rec.mean()
    ref_n = playback - playback.mean()
    corr = np.correlate(rec_n, ref_n, mode="full")
    lag = int(np.argmax(np.abs(corr)) - (len(ref_n) - 1))
    delay_ms = lag / RATE * 1000.0
    rec_rms = float(np.sqrt(np.mean(rec**2)))
    return lag, delay_ms, rec_rms


def main() -> None:
    print(f"Measuring speaker->mic delay at {RATE} Hz ({REPEATS} runs). Keep quiet...")
    samples: list[int] = []
    for i in range(REPEATS):
        lag, ms, rms = measure_once()
        flag = "  (mic heard ~nothing!)" if rms < 1e-3 else ""
        print(f"  run {i + 1}: {lag:5d} samples = {ms:6.1f} ms   mic_rms={rms:.4f}{flag}")
        samples.append(lag)

    arr = np.array(samples)
    print(
        f"\nmedian delay: {int(np.median(arr))} samples "
        f"= {np.median(arr) / RATE * 1000:.1f} ms   "
        f"(spread {arr.max() - arr.min()} samples)"
    )
    print("Set this as AEC_DELAY_SAMPLES (config) if hardcoding, or just use it to verify.")


if __name__ == "__main__":
    main()
