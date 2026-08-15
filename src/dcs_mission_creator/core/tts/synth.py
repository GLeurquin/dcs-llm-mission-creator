"""Backend-agnostic TTS facade.

`VoiceSynth` owns the on-disk render cache and wires rendered audio into
pydcs trigger rules via `mission.map_resource.add_resource_file` +
`dcs.action.SoundToAll / SoundToCoalition / SoundToGroup`.

The cache key combines `backend.fingerprint()` with the text, so swapping
voices or engines auto-invalidates without colliding with prior renders.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from dcs_mission_creator.core.tts.backend import VoiceBackend
from dcs_mission_creator.core.tts.piper import PiperBackend

if TYPE_CHECKING:
    from dcs.mission import Mission
    from dcs.triggers import TriggerRule

log = structlog.get_logger(__name__)

_DEFAULT_CACHE = Path("cache") / "voice"


class VoiceSynth:
    """Render-and-cache facade. Delegates synthesis to a `VoiceBackend`."""

    def __init__(
        self,
        backend: VoiceBackend | None = None,
        *,
        cache_dir: Path | None = None,
    ) -> None:
        self.backend: VoiceBackend = backend or PiperBackend()
        self.cache_dir = (cache_dir or _DEFAULT_CACHE).resolve()

    def _cache_path(self, text: str) -> Path:
        h = hashlib.sha256()
        h.update(self.backend.fingerprint().encode("utf-8"))
        h.update(b"\0")
        h.update(text.encode("utf-8"))
        return self.cache_dir / f"{h.hexdigest()[:16]}.wav"

    def render(self, text: str) -> Path:
        """Return a path to a WAV of `text`, rendering and caching if needed."""
        out = self._cache_path(text)
        if out.exists() and out.stat().st_size > 0:
            log.debug("voice cache hit", path=str(out))
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        log.info(
            "voice render",
            backend=self.backend.fingerprint(),
            path=str(out),
            chars=len(text),
        )
        self.backend.render_to_file(text, out)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(
                f"backend {self.backend.fingerprint()!r} produced no audio "
                f"for text={text!r} at {out}"
            )
        return out

    # -- DCS-side wiring ----------------------------------------------------

    def register(self, m: "Mission", text: str) -> str:
        """Render `text`, add it to the mission, return its in-`.miz` file name.

        For audio played from mission Lua (`trigger.action.outSound*`), which
        addresses sounds by file name — unlike the `SoundTo*` trigger actions
        the `attach_to_*` helpers use, which take a resource key.
        """
        wav = self.render(text)
        m.map_resource.add_resource_file(str(wav))
        return wav.name

    def attach_to_all(self, m: "Mission", rule: "TriggerRule", text: str) -> Path:
        """Render `text`, register with the mission, append `SoundToAll`."""
        from dcs import action

        wav = self.render(text)
        key = m.map_resource.add_resource_file(str(wav))
        rule.add_action(action.SoundToAll(key))
        return wav

    def attach_to_coalition(
        self,
        m: "Mission",
        rule: "TriggerRule",
        text: str,
        *,
        coalition: str,
    ) -> Path:
        """Render + register + append `SoundToCoalition` for "blue"/"red"."""
        from dcs import action

        if coalition not in ("blue", "red", "neutral"):
            raise ValueError(f"coalition must be blue/red/neutral, got {coalition!r}")
        wav = self.render(text)
        key = m.map_resource.add_resource_file(str(wav))
        rule.add_action(action.SoundToCoalition(coalition, key))
        return wav

    def attach_to_group(
        self,
        m: "Mission",
        rule: "TriggerRule",
        text: str,
        *,
        group_id: int,
    ) -> Path:
        """Render + register + append `SoundToGroup`."""
        from dcs import action

        wav = self.render(text)
        key = m.map_resource.add_resource_file(str(wav))
        rule.add_action(action.SoundToGroup(group_id, key))
        return wav
