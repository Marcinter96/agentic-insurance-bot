"""Pure-numpy acoustic echo canceller (NLMS adaptive filter).

The model's own voice comes out of the speaker and leaks back into the mic
~70-95 ms later (measured with voice_live.aec_probe). That echo makes the
server-side VAD think the user is barging in, and the bot talks over itself.

This removes it without native libraries: a normalized least-mean-squares
(NLMS) adaptive FIR filter learns the speaker->mic echo path and subtracts a
prediction of the echo from the mic signal.

    far  (reference) = what we sent to the speaker, resampled to the mic rate
    near (mic)       = what the mic captured (your voice + echo of `far`)
    out              = near - estimated_echo   (your voice, echo removed)

The filter length spans the echo delay + its tail, so the adaptive taps can
"find" the echo wherever it lands within the window — this tolerates the
run-to-run delay jitter we measured, so we do NOT depend on a precise delay
constant.

Everything is mono 16 kHz int16 little-endian PCM (the mic format), processed
one sample at a time inside short frames.
"""

from __future__ import annotations

import numpy as np


class EchoCanceller:
    """Mono NLMS echo canceller operating on int16 PCM byte frames."""

    def __init__(
        self,
        rate: int = 16000,
        filter_ms: float = 200.0,
        mu: float = 0.3,
        eps: float = 1e-6,
        dtd_threshold: float = 2.0,
    ) -> None:
        """Args:
        rate: sample rate of both near and far signals (Hz).
        filter_ms: adaptive filter length in ms; must comfortably exceed the
            speaker->mic delay (we measured ~70-95 ms, so 200 ms gives margin).
        mu: NLMS step size (0<mu<2). Higher = faster adaptation, less stable.
        eps: regularization to avoid divide-by-zero on silence.
        dtd_threshold: double-talk detector. Adaptation freezes for a frame
            when the residual error energy exceeds this multiple of the echo
            estimate energy (i.e. near-end speech is present) — otherwise the
            filter would try to cancel the user's own voice and diverge.
        """
        self.rate = rate
        self.taps = int(rate * filter_ms / 1000.0)
        self.mu = float(mu)
        self.eps = float(eps)
        self.dtd_threshold = float(dtd_threshold)

        # Adaptive filter weights and the rolling reference history (far).
        self._w = np.zeros(self.taps, dtype=np.float64)
        self._far_hist = np.zeros(self.taps, dtype=np.float64)

    @staticmethod
    def _to_float(pcm: bytes) -> np.ndarray:
        return np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0

    @staticmethod
    def _to_pcm(x: np.ndarray) -> bytes:
        x = np.clip(x, -1.0, 1.0)
        return (x * 32767.0).astype("<i2").tobytes()

    def process(self, near_pcm: bytes, far_pcm: bytes) -> bytes:
        """Cancel the echo of `far` from `near`. Returns cleaned PCM (== len near).

        `near_pcm` and `far_pcm` must be the same number of int16 samples. If
        `far` is shorter (no playback queued) it is zero-padded -> filter just
        passes the mic through while adapting toward zero.
        """
        near = self._to_float(near_pcm)
        far = self._to_float(far_pcm)
        n = len(near)
        if len(far) < n:
            far = np.concatenate([far, np.zeros(n - len(far))])

        out = np.empty(n, dtype=np.float64)
        w = self._w
        hist = self._far_hist
        eps = self.eps
        mu = self.mu

        # Decide ONCE per frame whether to adapt:
        #  - far must be active (speaker playing) — else there is no echo to learn.
        #  - near must not greatly exceed far (Geigel double-talk test) — else the
        #    user is talking and adapting would cancel their voice.
        far_energy = float(far @ far)
        near_energy = float(near @ near)
        far_active = far_energy > eps * n
        double_talk = near_energy > self.dtd_threshold * far_energy
        adapt = far_active and not double_talk

        for i in range(n):
            hist[1:] = hist[:-1]
            hist[0] = far[i]

            echo_est = float(w @ hist)
            err = near[i] - echo_est
            out[i] = err

            if adapt:
                norm = float(hist @ hist) + eps
                w += (mu * err / norm) * hist

        self._w = w
        self._far_hist = hist
        return self._to_pcm(out)

    def reset(self) -> None:
        """Forget the learned path (e.g. after a long silence / device change)."""
        self._w[:] = 0.0
        self._far_hist[:] = 0.0


def resample_24k_to_16k(pcm_24k: bytes) -> bytes:
    """Linear-resample mono int16 PCM from 24 kHz (speaker) to 16 kHz (mic rate).

    The reference signal must be at the mic's rate for the canceller to align
    it with the captured echo.
    """
    src = np.frombuffer(pcm_24k, dtype="<i2").astype(np.float64)
    if src.size == 0:
        return b""
    dst_len = int(round(src.size * 16000 / 24000))
    if dst_len <= 0:
        return b""
    src_idx = np.linspace(0, src.size - 1, dst_len)
    dst = np.interp(src_idx, np.arange(src.size), src)
    return dst.astype("<i2").tobytes()
