"""TTS backend interface.

Implement `fingerprint()` and `render_to_file()` to plug in a new engine
(Piper, Coqui, Kokoro, ElevenLabs, Azure). `VoiceSynth` is agnostic to the
concrete backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class VoiceBackend(Protocol):
    """Pluggable TTS engine. Two methods — that's the whole contract."""

    def fingerprint(self) -> str:
        """Return a stable string identifying engine + voice + tuning.

        Used as part of the cache key so changing voice or rate invalidates
        cached renders without colliding with other backends in the same
        cache directory.
        """
        ...

    def render_to_file(self, text: str, out_path: Path) -> None:
        """Synthesize `text` into a WAV at `out_path` (parent dir exists)."""
        ...
