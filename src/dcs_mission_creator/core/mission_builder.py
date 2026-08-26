"""Common base class for mission builders.

Concrete missions subclass `MissionBuilder`, set the class attributes `name`
(filesystem slug), `title` (display name) and `difficulty`, and implement
`_assemble` and `readme`. The `dcs-mission-creator` CLI discovers concrete
subclasses under `dcs_mission_creator.missions.*` and calls
`.generate(output_dir)` to produce both the `.miz` and a `README.md`.

The base owns the parts of the build that are the *same* for every mission and
that used to be documented conventions each mission re-implemented: seeding for
reproducibility, snapping take-off and landing waypoints to field elevation
once every flight exists, and saving. A mission supplies the middle.
"""

from __future__ import annotations

import hashlib
import random
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from dcs.mission import Mission

from dcs_mission_creator.core import datalink, dcs_install, dtc, kneeboard, waypoints
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.recon import publish as recon

if TYPE_CHECKING:
    from dcs.terrain.terrain import Terrain

    from dcs_mission_creator.map_overlay.query import MapOverlay


class MissionBuilder(ABC):
    name: str
    title: str

    #: Sets both how much of the enemy picture the F10 plan reveals and how the
    #: enemy AI fights. Declared here so it is part of the contract rather than
    #: an untyped string each mission happens to define.
    difficulty: Difficulty = Difficulty.TRAINED

    #: The theater, set by the subclass `__init__`.
    _terrain: Terrain

    def __init__(self, *, players: int = 1) -> None:
        if players < 1 or players > 4:
            raise ValueError(f"players must be 1..4, got {players}")
        self.players = players
        # Before any flight is built: pydcs caches its payload dirs on first use.
        dcs_install.configure()
        self._pin_runway_waypoint_distance()
        self._pin_onboard_numbers()

    @abstractmethod
    def _assemble(self, m: Mission) -> MapOverlay:
        """Build the whole mission into `m`.

        Returns the overlay the mission's positions came from, which the base
        uses to put take-off and landing waypoints on the terrain.
        """

    @abstractmethod
    def readme(self) -> str:
        """Return the README.md content (markdown) describing this mission."""

    def build_miz(self, miz_path: Path) -> None:
        """Assemble the mission, then finish and save it.

        Concrete on purpose. The first three finishing steps have to happen
        after the last flight exists and before the save, and all three are
        things pydcs leaves undone rather than things a mission decides:

        - base-waypoint snapping, because pydcs hard-codes take-off and landing
          altitudes to zero, which buries them under any field above sea level.
          That ordering used to live in CLAUDE.md as prose, with all six
          missions ending in the same three lines; a mission that forgot them
          shipped a broken jet.
        - departure speeds, because `add_runway_waypoint` hard-codes 108 kt at
          300 m AGL with no parameter to override it — below the stall speed of
          a loaded jet, so the AI left the runway at max alpha and full
          afterburner.
        - datalink identities, because pydcs writes neither track numbers nor
          the per-unit network table, so a coop flight spawned anonymous and
          could not see itself on the scope.

        The fourth finishing step is the mirror image: any data cartridge a
        mission armed is a *file inside the package*, and `Mission.save` writes
        a fixed set of zip entries with no hook for another one, so it goes in
        after the save.

        The fifth is the kneeboard (`core/kneeboard`) — files inside the
        package like the cartridge, since pydcs's own
        `add_aircraft_kneeboard` writes an entry path with an empty component in
        it. It runs after the save for the archive's sake and after every other
        step for the content's: the route card prints the take-off and landing
        altitudes `snap_base_waypoints` has just corrected.

        The sixth has a different reason again, and it is worth stating rather
        than letting the list above absorb it: a recon still (`core/recon`) is
        already inside the `.miz` as a briefing slide by the time we get here,
        because pydcs models briefing pictures. What it is *not* is next to the
        README, and the README embeds it with a relative link — so a copy lands
        beside the package. An asset alongside the mission, not a gap in what
        pydcs writes.
        """
        m = Mission(self._terrain)
        self._permit_crash_recovery(m)
        overlay = self._assemble(m)
        waypoints.snap_base_waypoints(m, overlay)
        waypoints.set_departure_speeds(m)
        datalink.assign_datalink_identities(m)
        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))
        dtc.write_cartridges(m, miz_path)
        kneeboard.publish(m, miz_path, overlay=overlay, title=self.title)
        recon.publish_stills(m, miz_path.parent)

    @staticmethod
    def _permit_crash_recovery(m: Mission) -> None:
        """Let a player who crashes pick an aircraft again instead of being done.

        Every player slot is `Skill.Client`, so a mission opens on the
        slot-selection screen — but crash recovery is off in the DCS simulation
        preset, and with it off a crash goes straight to the debriefing and the
        sortie is over. Enforcing the ME's "PERMIT CRASH RCVR" (`permitCrash`)
        in the mission's own forced options overrides whatever the player has
        set, so a crash returns to the slot list and the jet can be re-selected.
        Nothing else is forced: the rest of the gameplay options stay the
        player's.
        """
        m.forced_options.permit_crash = True

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
