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

import functools
import hashlib
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from dcs.mapping import LatLng, Point
from dcs.mission import Mission

from dcs_mission_creator.core import (
    datalink,
    dcs_install,
    dtc,
    join_up,
    kneeboard,
    loadout,
    mission_kit,
    radio,
    visibility,
    waypoints,
)
from dcs_mission_creator.core.difficulty import Difficulty
from dcs_mission_creator.core.loadout import Loadout
from dcs_mission_creator.core.map_draw import PlanOverlay
from dcs_mission_creator.core.recon import publish as recon
from dcs_mission_creator.core.tts import VoiceSynth
from dcs_mission_creator.core.weather import Weather

if TYPE_CHECKING:
    from dcs.terrain.terrain import Terrain

    from dcs_mission_creator.core.recon import ReconStill
    from dcs_mission_creator.map_overlay.query import MapOverlay


#: The fewest coop client slots a mission is built for, and it is two rather
#: than one on purpose. An F-16C has six weapon stations once the bags, the ECM
#: pod and the two sensor pods are on, and only three of them (3/7 and the
#: 2/8 pair) take anything but a missile — so one jet can carry the HARMs or the
#: bombs and never both, and a mission written for a single slot spends its
#: whole design budget deciding which half of its own frag to throw away.
#: Two slots is the smallest flight that can carry a tasking, and every mission
#: here now splits its fit across the pair (`core/loadout.py`).
MIN_PLAYERS = 2

#: The most coop client slots a mission is built for. Above
#: `mission_kit.MAX_FLIGHT_SIZE` the player flight is more than one group — a
#: DCS plane group holds four aircraft — which is what `mission_kit.player_flight`
#: is for, and why raising this number is not only a change to this line.
MAX_PLAYERS = 6


@dataclass(frozen=True)
class Assembled:
    """What a mission hands back so the base can finish the briefing.

    `overlay` is what `_assemble` always returned. `briefed_threats` is the list
    `_draw_plan` produced, and it is here rather than loaded by the mission
    because eight copies of `_load_cartridge` had byte-identical bodies: which
    rings reach the cockpit is not a decision, it is the same two calls on
    whatever the map drew.
    """

    overlay: MapOverlay
    #: The estimates `PlanOverlay.threat` returned, by way of `dtc.briefed`.
    #: Empty is a supported answer — a mission that briefs no ring, or whose
    #: only air defence moves.
    briefed_threats: Sequence[dtc.ThreatPoint] = ()


class MissionBuilder(ABC):
    name: str
    title: str

    #: Sets both how much of the enemy picture the F10 plan reveals and how the
    #: enemy AI fights. Declared here so it is part of the contract rather than
    #: an untyped string each mission happens to define.
    difficulty: Difficulty = Difficulty.TRAINED

    #: The theater, as the pydcs class rather than an instance — so a mission
    #: states it beside `name` and `title` instead of in an `__init__` whose
    #: only other line was the voice synth.
    #:
    #: Typed as a factory rather than `type[Terrain]` because pydcs's base
    #: `Terrain.__init__` takes five arguments its concrete subclasses fill in;
    #: what a mission supplies is something that yields a terrain when called.
    terrain: Callable[[], Terrain]

    #: The two coalition task panels, as the mission editor calls them. Plain
    #: strings in every mission here; read through `blue_task_text` /
    #: `red_task_text` so one that needs to interpolate a callsign or a
    #: difficulty still can.
    blue_task: str
    red_task: str

    #: When the mission starts, map-local. pydcs serialises the hour and minute
    #: verbatim and DCS reads the field as map-local, so `tzinfo` is inert:
    #: write the local time you want. Every mission had a `_set_time` method
    #: whose entire body was this assignment and whose docstring was that
    #: sentence, eight times.
    start_time: datetime

    #: The weather, as a record rather than fourteen assignments. `Weather` is
    #: frozen and `apply` does not mutate it, so it is safe on the class.
    weather: Weather

    #: The recon still this mission published, if it published one. A class
    #: attribute rather than an `__init__` line because `None` is immutable and
    #: three missions were writing the same line; `build_miz` is what actually
    #: ships it, via `recon.publish_stills`.
    _still: ReconStill | None = None

    def __init__(self, *, players: int = MIN_PLAYERS) -> None:
        if players < MIN_PLAYERS or players > MAX_PLAYERS:
            raise ValueError(
                f"players must be {MIN_PLAYERS}..{MAX_PLAYERS}, got {players}"
            )
        self.players = players
        # Before any flight is built: pydcs caches its payload dirs on first use.
        dcs_install.configure()
        self._pin_runway_waypoint_distance()
        self._pin_onboard_numbers()

    @abstractmethod
    def _assemble(self, m: Mission, plan: PlanOverlay) -> Assembled:
        """Build the whole mission into `m`, and hand back what finishing needs.

        `plan` is the mission's F10 overlay, constructed by the base and passed
        in rather than built here. Half the missions used to build it before
        `_setup_airports` because their route geometry reads `plan.estimate`,
        and half just before `_draw_plan` — an ordering hazard each of them
        carried a comment about. Constructing it earlier than any of them is a
        no-op: `PlanOverlay.__init__` stores three references and scans for a
        layer, with no RNG draw, no dictionary allocation and no mutation.
        """

    @functools.cached_property
    def _terrain(self) -> Terrain:
        """This mission's terrain, built once.

        Lazy rather than eager so a builder can be constructed — by
        `dcs-mission-creator list`, by a test — without paying for it.
        """
        return self.terrain()

    @functools.cached_property
    def _voice(self) -> VoiceSynth:
        """The mission's text-to-speech renderer, built on first use.

        Every mission built one in `__init__` with no arguments. It is lazy for
        the same reason as the terrain and because `PiperBackend` loads its ONNX
        model on demand, so constructing a builder costs nothing.
        """
        return VoiceSynth()

    def blue_task_text(self) -> str:
        """The friendly coalition's task panel. Override to compute it."""
        return self.blue_task

    def red_task_text(self) -> str:
        """The enemy coalition's task panel. Override to compute it."""
        return self.red_task

    @abstractmethod
    def _in_game_briefing(self) -> str:
        """The mission's in-game description panel, as plain text.

        Every mission already had this method with this name and signature; it
        is abstract so that an existing universal convention is part of the
        contract rather than something eight files happen to agree on.
        """

    def start_time_for(self, m: Mission) -> datetime:
        """When this mission starts. Override to compute it.

        The attribute is the shorthand for the eight missions whose answer is a
        literal; this is here so a mission whose time is derived — from a
        sunrise calculation, from its difficulty — is still expressible without
        the base telling it what shape the answer has to be.
        """
        return self.start_time

    def weather_for(self, m: Mission) -> Weather:
        """This mission's weather. Override to compute it, for the same reason."""
        return self.weather

    def at(self, lat: float, lng: float) -> Point:
        """One `(lat, lng)` constant as a world `Point` on this mission's theater.

        Public and on the base because a mission's fixed positions should be
        written in degrees: `daryal_run` shipped two waypoints inside a mountain
        as raw DCS metres, and there is no way to read `Point(-200000, 863000)`
        and see a mountain. A `(name, lat, lng, agl)` table can be checked
        against a chart.
        """
        return Point.from_latlng(LatLng(lat, lng), self._terrain)

    def recon_figure_md(self) -> str:
        """The recon-still figure block for `readme()`, or nothing.

        Empty rather than raising, so `readme()` still works on a builder whose
        `_assemble` has not run, and at the difficulties whose reveal policy
        withholds the imagery entirely.
        """
        return "" if self._still is None else self._still.markdown()

    def slot_summary(self, flight: str) -> str:
        """The `**Players:**` line: how many slots, and what flight they are in.

        Above `mission_kit.MAX_FLIGHT_SIZE` the player flight is more than one
        DCS group, and a briefing that still says "Dodge" while the slot list
        offers `Dodge` and `Dodge 2` is a briefing contradicting its own mission
        file. `readme()` holds no `Mission`, so the naming comes off
        `mission_kit.section_names`, which is the same table `player_flight`
        builds the groups from.
        """
        sizes = mission_kit.section_sizes(self.players)
        if len(sizes) == 1:
            return f"{self.players} coop slot(s)"
        names = mission_kit.section_names(flight, len(sizes))
        parts = ", ".join(f"{n} ({s})" for n, s in zip(names, sizes))
        return f"{self.players} coop slot(s), flown as {len(sizes)} sections: {parts}"

    def loadout_table(self, flight: str, loadouts: Sequence[Loadout]) -> str:
        """The README's slot-by-slot loadout table for `flight`.

        A mission declares its fits once, as a module constant, and hands the
        same tuple to `_spawn_player` and to both briefing views — so the jet
        that the briefing says is carrying the HARMs is the jet that has them on
        3 and 7. `readme()` holds no `Mission`, which is why the slot names come
        off `mission_kit.slot_names` rather than off the built groups.
        """
        return loadout.table(
            mission_kit.slot_names(flight, self.players),
            loadout.assign(loadouts, self.players),
        )

    def loadout_brief(
        self, flight: str, loadouts: Sequence[Loadout], *, indent: str = "  "
    ) -> str:
        """The same table as plain text, for `set_description_text`."""
        return loadout.block(
            mission_kit.slot_names(flight, self.players),
            loadout.assign(loadouts, self.players),
            indent=indent,
        )

    def air_to_air_shots(self, loadouts: Sequence[Loadout]) -> int:
        """The whole player flight's air-to-air magazine, off the actual fits.

        The force-balance rule in CLAUDE.md — a mission may not task more kills
        than the flight carries weapons for — divides this by two. Reading it
        off the stores rather than off a per-mission constant is what keeps a
        mission that scales its opposition with `--players` honest once the
        slots stop carrying the same thing as each other.
        """
        return loadout.shots(loadout.assign(loadouts, self.players))

    @abstractmethod
    def readme(self) -> str:
        """Return the README.md content (markdown) describing this mission."""

    def build_miz(self, miz_path: Path) -> None:
        """Assemble the mission, then finish and save it.

        Concrete on purpose. The first five finishing steps have to happen
        after the last flight exists and before the save, and all five are
        things pydcs leaves undone rather than things a mission decides:

        - the package hold (`core/join_up`), because every AI flight launches at
          `TriggerStart` by default and a player who is still aligning an INS
          can never join up with one. A mission that forgot it shipped a
          briefing about escorting a flight that was already over the target.
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
        - radio frequencies (`core/radio`), because `awacs_flight` and
          `refuel_flight` spend their `frequency=` argument on a waypoint task
          and leave the group's own field on pydcs's 251 MHz default, which is
          the field a player's radio has to match. Every tanker in this project
          was briefed on a frequency it was not on.

        The sixth finishing step is the mirror image: any data cartridge a
        mission armed is a *file inside the package*, and `Mission.save` writes
        a fixed set of zip entries with no hook for another one, so it goes in
        after the save.

        The seventh is the kneeboard (`core/kneeboard`) — files inside the
        package like the cartridge, since pydcs's own
        `add_aircraft_kneeboard` writes an entry path with an empty component in
        it. It runs after the save for the archive's sake and after every other
        step for the content's: the route card prints the take-off and landing
        altitudes `snap_base_waypoints` has just corrected.

        The eighth has a different reason again, and it is worth stating rather
        than letting the list above absorb it: a recon still (`core/recon`) is
        already inside the `.miz` as a briefing slide by the time we get here,
        because pydcs models briefing pictures. What it is *not* is next to the
        README, and the README embeds it with a relative link — so a copy lands
        beside the package. An asset alongside the mission, not a gap in what
        pydcs writes.
        """
        m, overlay = self.assemble()
        miz_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(miz_path))
        dtc.write_cartridges(m, miz_path)
        kneeboard.publish(m, miz_path, overlay=overlay, title=self.title)
        recon.publish_stills(m, miz_path.parent)

    def assemble(self) -> tuple[Mission, MapOverlay]:
        """The finished mission and its overlay, up to but not including the save.

        Everything in `build_miz`'s first list happens here, in that order, so
        this is the mission exactly as it will be written — corrected altitudes,
        corrected departure speeds, datalink identities, tuned radios and the
        held package all applied. What is missing is only the three things that
        are *files inside the archive* and therefore cannot exist before it.

        It is split out for `core/audit.py`, which needs the built mission and
        nothing on disk: a full `generate` renders voice, writes a five-megabyte
        archive and draws kneeboard pages, and none of that tells you whether a
        waypoint is inside a mountain. Splitting it rather than letting the
        audit re-run the finishing steps itself is the point — the ordering
        above is load-bearing and lives in exactly one place, so an audit cannot
        report on a mission subtly different from the one that ships.

        Seeds the RNG for the same reason `generate` does, so a bare
        `assemble()` is as reproducible as a build. Seeding twice before any
        draw is harmless: it sets the same state from the same slug.
        """
        self._seed_rng()
        m = Mission(self._terrain)
        self._permit_crash_recovery(m)
        m.start_time = self.start_time_for(m)
        self.weather_for(m).apply(m)
        plan = PlanOverlay(m, self.difficulty)
        out = self._assemble(m, plan)
        overlay = out.overlay
        self._finish_briefing(m, plan, out)
        self._remark_loadouts(m)
        join_up.hold_package_for_player(m)
        waypoints.snap_base_waypoints(m, overlay)
        waypoints.set_departure_speeds(m)
        datalink.assign_datalink_identities(m)
        radio.tune_working_frequencies(m)
        return m, overlay

    def _finish_briefing(self, m: Mission, plan: PlanOverlay, out: Assembled) -> None:
        """Hide the enemy, load the briefed picture, and write the panels.

        The four steps every mission's `_assemble` used to end with, in the
        order it ended with them. They are here rather than in the missions for
        the reason the rest of `assemble` is: none of them is a decision. Eight
        `_load_cartridge` methods had byte-identical two-line bodies under
        ninety-four lines of docstring, and eight `_conceal_red` methods had no
        payload at all.

        **Overridable, and every line degrades rather than raises.** The base
        owning a step means it cannot be forgotten, not that it cannot be
        changed: a mission that wants a group left visible on purpose, or a
        briefing written some other way, overrides this and calls `super()` for
        the parts it still wants.
        """
        visibility.conceal_coalition(m, self._enemy_coalition(m))
        self._load_cartridge(m, plan, out)
        m.set_description_text(self._in_game_briefing())
        m.set_description_bluetask_text(self.blue_task_text())
        m.set_description_redtask_text(self.red_task_text())
        m.set_sortie_text(self.title)

    @staticmethod
    def _load_cartridge(m: Mission, plan: PlanOverlay, out: Assembled) -> None:
        """The briefed picture, into the cockpit that can hold it and the card.

        Two calls with no distinct payload across eight missions: the briefed
        rings as pre-planned threats, and the rest of the F10 plan as
        steerpoints and GEO lines. The map and the cockpit are one briefing
        because they are read from one place.

        Both need a player-flown F-16C, which is the only module in DCS that
        draws a pre-planned threat ring or reads a steerpoint cartridge. Every
        mission here flies one — and making the calls unconditional is exactly
        how "F-16C" would become part of the base's contract without anyone
        deciding it. A package with no Viper still gets the threat list
        recorded, because `core/kneeboard`'s threat block is then the *only*
        place those coordinates exist for that pilot.
        """
        if any(
            unit.unit_type.id == dtc.AIRCRAFT.id
            for group in mission_kit.player_groups(m)
            for unit in group.units
        ):
            dtc.arm_hsd_threats(m, out.briefed_threats, overlay=out.overlay)
            dtc.arm_plan(m, plan, overlay=out.overlay)
        elif out.briefed_threats:
            dtc.record_briefed(m, out.briefed_threats)

    @staticmethod
    def _enemy_coalition(m: Mission) -> str:
        """Whichever side the client slots are not on.

        Derived rather than assumed, so a mission that flies red needs no
        special case — the same rule `core/audit.py`'s concealment check uses.
        """
        ours = {
            side
            for side, group in mission_kit.flying_groups_by_side(m)
            if mission_kit.is_client(group)
        }
        return "red" if "red" not in ours else "blue"

    @staticmethod
    def _remark_loadouts(m: Mission) -> None:
        """Put the flight's fit split on the kneeboard, once, for every mission.

        What a pilot cannot get from his own cockpit is what the *other* jet is
        carrying, and that is the whole point of a split flight — the SMS page
        answers for his own stores and nothing anywhere answers for his
        wingman's. It is written here rather than in each mission for the same
        reason the waypoint snap is: a mission that forgot it would ship a pair
        whose two halves each believe they are the strike.
        """
        for flight, assignment in loadout.assignments(m):
            for line in loadout.remark_lines(flight, assignment):
                kneeboard.remark(m, line)

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
