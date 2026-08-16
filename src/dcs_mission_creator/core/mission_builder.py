"""Common base class for mission builders.

Concrete missions subclass `MissionBuilder`, set the class attributes `name`
(filesystem slug) and `title` (display name), and implement `build_miz` and
`readme`. The `dcs-mission-creator` CLI discovers concrete subclasses under
`dcs_mission_creator.missions.*` and calls `.generate(output_dir)` to produce
both the `.miz` and a `README.md` in the same folder.
"""

from __future__ import annotations

import hashlib
import random
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
        self._pin_runway_waypoint_distance()
        self._pin_onboard_numbers()

    @abstractmethod
    def build_miz(self, miz_path: Path) -> None:
        """Write the `.miz` file at `miz_path`."""

    @abstractmethod
    def readme(self) -> str:
        """Return the README.md content (markdown) describing this mission."""

    @staticmethod
    def _pin_runway_waypoint_distance(distance_m: int = 7_000) -> None:
        """Remove the one pydcs randomness a seed cannot reach.

        `FlyingGroup.add_runway_waypoint` declares
        `distance=random.randrange(6000, 8000, 100)` as a **default argument**,
        so the value is drawn once when `dcs.unitgroup` is imported — before any
        seeding can run — and then reused for the rest of the process. The
        effect was that every flight's take-off waypoint sat somewhere new on
        each build while staying consistent within a build. Pin it instead.
        """
        from dcs.unitgroup import FlyingGroup

        fn = FlyingGroup.add_runway_waypoint
        defaults = fn.__defaults__ or ()
        if defaults:
            fn.__defaults__ = defaults[:-1] + (distance_m,)

    @staticmethod
    def _pin_onboard_numbers() -> None:
        """Make aircraft tail numbers reproducible.

        `Country.next_onboard_num` picks one with `set.pop()` over a set of
        strings, so the choice follows string hashing and changes on every
        interpreter run — the last thing keeping two builds of the same mission
        from being identical. Hand out the lowest free number instead.
        """
        from dcs.country import Country

        def next_onboard_num(self: Country) -> str:
            free = {f"{x:03}" for x in range(10, 999)} - self.unused_onboard_numbers
            tailnum = min(free)
            self.reserve_onboard_num(tailnum)
            return tailnum

        Country.next_onboard_num = next_onboard_num  # ty: ignore[invalid-assignment]

    def _seed_rng(self) -> None:
        """Make one mission's build reproducible run to run.

        pydcs draws from the stdlib `random` module for things like aircraft
        tail numbers, so an unseeded build differs on every invocation. Seed
        from the mission slug: each mission is stable, and two missions still
        get different draws. The overlay's own sampling is seeded separately,
        inside `MapOverlay.find_placement`.
        """
        digest = hashlib.blake2b(self.name.encode(), digest_size=8).digest()
        random.seed(int.from_bytes(digest, "big"))

    def generate(self, output_dir: Path) -> tuple[Path, Path]:
        """Build the `.miz` and write `README.md` into `output_dir`."""
        self._seed_rng()
        output_dir.mkdir(parents=True, exist_ok=True)
        miz_path = output_dir / f"{self.name}.miz"
        readme_path = output_dir / "README.md"
        self.build_miz(miz_path)
        readme_path.write_text(self.readme())
        return miz_path, readme_path
