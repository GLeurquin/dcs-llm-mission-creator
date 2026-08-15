"""Common base class for mission builders.

Concrete missions subclass `MissionBuilder`, set the class attributes `name`
(filesystem slug) and `title` (display name), and implement `build_miz` and
`readme`. The `dcs-mission-creator` CLI discovers concrete subclasses under
`dcs_mission_creator.missions.*` and calls `.generate(output_dir)` to produce
both the `.miz` and a `README.md` in the same folder.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from dcs_mission_creator.core import dcs_install


class MissionBuilder(ABC):
    name: str
    title: str

    def __init__(self, *, players: int = 1) -> None:
        if players < 1 or players > 4:
            raise ValueError(f"players must be 1..4, got {players}")
        self.players = players
        # Before any flight is built: pydcs caches its payload dirs on first use.
        dcs_install.configure()

    @abstractmethod
    def build_miz(self, miz_path: Path) -> None:
        """Write the `.miz` file at `miz_path`."""

    @abstractmethod
    def readme(self) -> str:
        """Return the README.md content (markdown) describing this mission."""

    def generate(self, output_dir: Path) -> tuple[Path, Path]:
        """Build the `.miz` and write `README.md` into `output_dir`."""
        output_dir.mkdir(parents=True, exist_ok=True)
        miz_path = output_dir / f"{self.name}.miz"
        readme_path = output_dir / "README.md"
        self.build_miz(miz_path)
        readme_path.write_text(self.readme())
        return miz_path, readme_path
