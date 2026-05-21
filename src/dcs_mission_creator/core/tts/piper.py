"""Piper TTS backend (default).

Wraps [piper1-gpl](https://github.com/OHF-voice/piper1-gpl) — a fast, local,
neural ONNX-based TTS engine. CPU-friendly, sub-realtime on a laptop, no
GPU required. Voice models are downloaded from HuggingFace on first use.

Default voice: `en_US-danny-low` (neutral US male, narrator-grade).
Pass a different `voice` string to use any of the voices listed at
https://huggingface.co/rhasspy/piper-voices (e.g. `en_GB-alan-medium`,
`en_US-ryan-high`).
"""

from __future__ import annotations

import wave
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DEFAULT_VOICE = "en_US-danny-low"
_DEFAULT_MODEL_DIR = Path("cache") / "voice" / "models"


@dataclass
class PiperBackend:
    """Default TTS backend (piper1-gpl, neural ONNX).

    Args:
        voice: Piper voice name in the form ``<lang>-<name>-<quality>``
            (e.g. ``en_US-danny-low``). Downloaded on first use.
        model_dir: where to store/find the `.onnx` and `.onnx.json` files.
            Default: ``cache/voice/models/`` at the project root.
        length_scale: speech rate multiplier; >1.0 = slower, <1.0 = faster.
            ``None`` keeps the model's default.
        noise_scale, noise_w: pitch / phoneme-duration jitter. ``None`` keeps
            the model defaults (recommended).
    """

    voice: str = DEFAULT_VOICE
    model_dir: Path = field(default_factory=lambda: _DEFAULT_MODEL_DIR)
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    _voice_obj: object = field(default=None, init=False, repr=False)

    def fingerprint(self) -> str:
        ls = f"{self.length_scale:.2f}" if self.length_scale is not None else "def"
        ns = f"{self.noise_scale:.2f}" if self.noise_scale is not None else "def"
        nw = f"{self.noise_w:.2f}" if self.noise_w is not None else "def"
        return f"piper|{self.voice}|{ls}|{ns}|{nw}"

    def _ensure_model(self) -> Path:
        """Download the voice model on demand, return path to the `.onnx`."""
        from piper.download_voices import download_voice

        self.model_dir.mkdir(parents=True, exist_ok=True)
        onnx = self.model_dir / f"{self.voice}.onnx"
        cfg = self.model_dir / f"{self.voice}.onnx.json"
        if not (onnx.exists() and cfg.exists()):
            log.info("piper voice download", voice=self.voice, dir=str(self.model_dir))
            download_voice(self.voice, self.model_dir)
        return onnx

    def _voice_lazy(self):
        if self._voice_obj is None:
            from piper import PiperVoice

            onnx = self._ensure_model()
            log.info("piper voice load", path=str(onnx))
            self._voice_obj = PiperVoice.load(onnx)
        return self._voice_obj

    def _syn_config(self):
        from piper.config import SynthesisConfig

        kw = {}
        if self.length_scale is not None:
            kw["length_scale"] = self.length_scale
        if self.noise_scale is not None:
            kw["noise_scale"] = self.noise_scale
        if self.noise_w is not None:
            kw["noise_w_scale"] = self.noise_w
        return SynthesisConfig(**kw) if kw else None

    def render_to_file(self, text: str, out_path: Path) -> None:
        voice = self._voice_lazy()
        with wave.open(str(out_path), "wb") as wf:
            voice.synthesize_wav(text, wf, syn_config=self._syn_config())
