"""Text-to-speech for in-mission voice lines.

Public surface:

- `VoiceBackend` — Protocol every TTS engine implements.
- `VoiceSynth` — backend-agnostic facade: caches WAV renders on disk and
  attaches `SoundTo*` actions to pydcs trigger rules.
- `PiperBackend` — default backend wrapping
  [piper1-gpl](https://github.com/OHF-voice/piper1-gpl). Neural, local,
  CPU-friendly.

Swap engine by passing a different `VoiceBackend` to `VoiceSynth(...)`.
"""

from dcs_mission_creator.core.tts.backend import VoiceBackend
from dcs_mission_creator.core.tts.piper import PiperBackend
from dcs_mission_creator.core.tts.synth import VoiceSynth

__all__ = ["PiperBackend", "VoiceBackend", "VoiceSynth"]
