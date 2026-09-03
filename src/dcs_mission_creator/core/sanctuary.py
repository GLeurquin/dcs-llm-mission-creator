"""Somewhere to run to — for both sides (project-owned helper).

Every mission in this project used to send the player deep and give him nowhere
to go when it went wrong. Measured across all six: not one friendly SAM, not
one AAA piece, not one blue air-defence group of any kind. The recovery field
was bare ground with a runway on it, so a MiG that chased a bingo-fuel, out-of-
missiles jet home followed it all the way to the flare and shot it in the
overhead — and nothing on the F10 map, in the DED or on the kneeboard marked a
single square kilometre where that could not happen.

That is a design defect rather than a missing feature, and it shows up as two
separate things going wrong at once:

- **Disengaging is not a decision.** A player who correctly reads a fight as
  lost — three bandits, two missiles, 1,800 kg of fuel — has no move that
  changes the odds. Running is a slower version of dying, so the only playable
  line is to press a losing merge. Every mission's threat layout is priced on
  the assumption the player *can* leave (see `core/frontline.py`, which is
  entirely about pricing the ways in), and none of them held up the other end
  of that.
- **There is nothing to price pursuit against.** A red interceptor that follows
  the player 150 km into friendly airspace pays nothing for it. It should be the
  mistake it really is, and the way to make it one is not to script the MiG — it
  is to put a battery where the MiG has to fly.

So a sanctuary is a **defended** place, not a friendly-coloured one: an area SAM
whose envelope covers the recovery field, point defence on the field itself, and
— on the player's side — a marshal leg inside the envelope with a name, told to
every channel the player reads. Nothing is scripted: the defence is that the
missiles are real and both sides can see how far they reach.

## Both sides get one, and the difference is the reveal, not the geometry

The second half of the same argument runs the other way. An enemy airbase with
nothing on it is a free kill: the player chases a damaged MiG home, strafes it
on the roll-out, and the mission's whole air threat can be dismantled on the
ground by a jet with two missiles left. Worse, it makes the enemy's own bingo
RTB — which `tasking.apply_ai_difficulty` switches on for every red flight —
into a death sentence rather than a withdrawal, so the red side has no way to
break contact either. A defended red field turns "chase him home" from the
obviously correct move into a decision with a price on it, and it does so with
the same missiles rather than with a scripted rule.

What is **not** symmetric is what the player is told, and that split is the
whole reason this module knows about `core/map_draw.py` at all:

| | friendly sanctuary | enemy sanctuary |
|---|---|---|
| F10 drawing | `PlanOverlay.umbrella` — precise at every difficulty | `PlanOverlay.threat` — estimated, per the reveal policy |
| cartridge | the marshal leg and any alternate, as steerpoints; no threat point | a pre-planned threat point via `dtc.briefed` |
| marshal leg | drawn and given a steerpoint | none — we do not brief their holding pattern |
| kneeboard | a REMARKS line naming the cover | the route card's threat block, like any belt |

A battery the player's own side emplaced is not intelligence, so coarsening it
would model an ignorance nobody has — and it would break the one thing the ring
exists for, since a pilot who is hit and low on fuel cannot use an envelope
drawn 6 km off truth. A battery on the enemy's field is intelligence like any
other and goes through the same policy as every other red ring in the mission.

**The invariant that makes any of this safe is `keep_clear`.** An area SAM is a
mission-warping object — a Patriot has 100 km of reach, further than four of the
six missions' entire ingress — and an umbrella that touches the AO does not give
anybody a refuge, it deletes the mission. On the red side the failure is worse
and quieter: put a ring on the enemy field that also covers the target, and the
mission the player was briefed for is not the one that got built. So the reach
comes off the F-16C's own threat table (`core/dtc.py`, the same rows the
cartridge is written from, so nobody re-types a range) and `build_sanctuary`
**raises** if the envelope reaches within `clearance_m` of anything the mission
names as having to stay in play. Pick the system to fit the geometry; do not
pick the biggest one and hope.

Design rule, as everywhere else in `core/`: absolute world `Point` / pydcs
`Airport` and `Country` in, built groups out. No faction abstraction, no
policy — which field, which system and what the briefing says about it are the
mission's decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Protocol, Sequence

import structlog
from dcs.drawing.icon import StandardIcon
from dcs.templates import VehicleTemplate
from dcs.unit import Skill
from dcs.unitgroup import VehicleGroup
from dcs.vehicles import AirDefence

from dcs_mission_creator.core import air_defense as ad, dtc, mission_kit
from dcs_mission_creator.core.placement import NO_FOREST, snap_units_clear

if TYPE_CHECKING:
    from dcs.country import Country
    from dcs.mapping import Point
    from dcs.mission import Mission
    from dcs.terrain.terrain import Airport, Terrain
    from dcs.triggers import TriggerZoneCircular
    from dcs.unittype import VehicleType

    from dcs_mission_creator.core.map_draw import PlanOverlay
    from dcs_mission_creator.core.tts import VoiceSynth
    from dcs_mission_creator.map_overlay.query import MapOverlay

log = structlog.get_logger(__name__)

# How far off the field the area battery is emplaced, and which side.
#
# Doctrine puts a point-defence battery between the asset and the threat, so its
# envelope is pushed toward the axis the raid comes down rather than centred on
# the runway. That costs a few kilometres of the `keep_clear` margin, which is
# why the check below measures from the battery's own position and not from the
# airfield reference point — the difference is small but it is in the direction
# that matters.
_BATTERY_OFFSET_M = 4_500.0
#: Steps in, along the same bearing, when the doctrinal position is unusable.
#:
#: Airfields are on coasts. Sochi-Adler sits on the Black Sea shore with the
#: threat axis running out over the water, so the doctrinal offset put the whole
#: battery 4.5 km out to sea — and `snap_units_clear` could not save it, because
#: every cell within its 250 m search radius was also water (eight units, eight
#: "no clear spot found, vegetation=WATER" warnings, eight vehicles floating).
#: Walking the offset back toward the field is the honest correction: a battery
#: on the field itself is a real siting, one in the sea is not, and moving it
#: *sideways* to find land would silently take it off the axis it is there to
#: cover. Zero is included, so the fallback of last resort is the airfield
#: reference point, which is on land by construction.
_BATTERY_OFFSET_STEPS_M = (_BATTERY_OFFSET_M, 3_000.0, 1_500.0, 0.0)
#: Point-defence sections sit on the field, spread round the reference point.
_POINT_DEFENCE_RING_M = 1_800.0
#: How wide the area battery is spread. `VehicleTemplate` builds it inside 100 m.
_AREA_SITE_FOOTPRINT_M = 400.0
#: The marshal leg's hub sits this far off the field, away from the threat —
#: unless the envelope is too small to hold it, in which case it shrinks.
#:
#: It has to shrink because the leg is only useful *inside* the cover. A NASAMS
#: sanctuary reaches 15 km and its battery is emplaced 4.5 km up the threat axis,
#: so a hub 14 km down the other side puts both ends of the race-track 18-19 km
#: from the launchers — outside the umbrella, which makes the one drawing whose
#: whole purpose is "hold here and nothing can reach you" a lie. `eastern_shield`
#: shipped exactly that at Gaziantep.
_MARSHAL_STANDOFF_M = 14_000.0
#: Fraction of the envelope the furthest point of the leg may reach. Short of the
#: edge, because the edge of a missile envelope is where it stops working.
_MARSHAL_FIT = 0.7


class AreaBuilder(Protocol):
    """How an area battery is built. Mirrors `air_defense.SiteBuilder`.

    `overlay` + `terrain` are threaded through rather than left to the
    `disperse_site` pass afterwards, because a `core/air_defense.py` builder does
    its own `snap_units_clear` and warns when it is handed neither — a warning
    that used to fire on every sanctuary built from a project spec.
    """

    def __call__(
        self,
        m: "Mission",
        country: "Country",
        position: "Point",
        heading: float,
        *,
        skill: Skill,
        overlay: "MapOverlay | None",
        terrain: "Terrain | None",
    ) -> VehicleGroup: ...


@dataclass(frozen=True)
class Battery:
    """One area system: what to call it, how far it reaches, how to build it.

    `system` is the F-16C's own `THREAT_PTS` row, which is where the reach comes
    from. Reusing it rather than typing a radius means the ring the player is
    shown, the clearance check and — on the red side — the range the jet prints
    on the HSD and the kneeboard are one figure. It is also why `NASAMS` here is
    15 km rather than the 25 km the brochure says: 15 km is what DCS models, and
    the jet's own table is the authority on that.

    `point_defence` is the launcher that goes on the field itself, and it comes
    with the area system rather than being a separate argument because the two
    are not independent — a Hawk battery implies a NATO-equipped field and an
    S-125 a Russian one, and a mission should not have to state the obvious half
    of that pairing.
    """

    name: str
    system: dtc.ThreatSystem
    build: AreaBuilder
    #: Self-cueing SHORAD sited on the field, one group per section.
    point_defence: "type[VehicleType]"
    #: What to call those sections on the map and in the group name.
    point_defence_name: str
    #: How wide to disperse the built area site.
    footprint_m: float = _AREA_SITE_FOOTPRINT_M

    @property
    def radius_m(self) -> float:
        """The envelope the briefing may claim, straight off the jet's table."""
        return self.system.radius_m


# -- how each area system gets built -----------------------------------------
#
# Four of the six systems here have no `core/air_defense.py` spec, because pydcs
# already ships a template for them and this project's rule is to use it (see
# that module's docstring). The templates are what make these wrappers necessary
# rather than a table entry: they return `None`, they ignore the country they are
# handed, and they build the whole battery inside 100 m.


def _from_template(
    call: Callable[..., None], owner: Optional[str], what: str
) -> AreaBuilder:
    """Adapt a `VehicleTemplate` site builder to the `Battery.build` signature.

    `owner` is the country the template hard-codes, or `None` if it takes the
    one it is given. `VehicleTemplate.USA.hawk_site` and `patriot_site` both do
    `mission.country("USA")` internally and ignore their argument, so a mission
    that asked for a Turkish or Israeli battery would silently get an American
    one filed under the USA — hence the refusal rather than a warning.
    """

    def build(
        m: "Mission",
        country: "Country",
        position: "Point",
        heading: float,
        *,
        skill: Skill,
        overlay: "MapOverlay | None" = None,
        terrain: "Terrain | None" = None,
    ) -> VehicleGroup:
        # A template does no terrain check of its own; `disperse_site` in
        # `build_sanctuary` is handed the same overlay and does it there.
        del overlay, terrain
        if owner is not None and country.name != owner:
            raise ValueError(
                f"{what} is built from pydcs's VehicleTemplate, which hard-codes "
                f"mission.country({owner!r}) as the owner — it cannot build one "
                f"for {country.name!r}. Pick a Battery whose builder is this "
                f"project's own, or add a spec to core/air_defense.py."
            )
        before = len(m.country(country.name).vehicle_group)
        call(m, position, heading, prefix="", skill=skill)
        groups = m.country(country.name).vehicle_group
        if len(groups) != before + 1:
            raise RuntimeError(
                f"{what} template appended {len(groups) - before} groups"
            )
        return groups[-1]

    return build


def _from_builder(call: ad.SiteBuilder) -> AreaBuilder:
    """Adapt a `core/air_defense.py` builder, which already disperses and snaps."""

    def build(
        m: "Mission",
        country: "Country",
        position: "Point",
        heading: float,
        *,
        skill: Skill,
        overlay: "MapOverlay | None" = None,
        terrain: "Terrain | None" = None,
    ) -> VehicleGroup:
        return call(
            m, country, position, heading, skill=skill, overlay=overlay, terrain=terrain
        )

    return build


# Blue. Avenger over Stinger teams for the point defence because it carries its
# own FLIR and cues itself — a MANPADS section on a field is a decoration until
# something flies directly overhead.
HAWK = Battery(
    "Hawk",
    dtc.HAWK,
    _from_template(VehicleTemplate.USA.hawk_site, "USA", "Hawk"),
    AirDefence.M1097_Avenger,
    "Avenger",
)
PATRIOT = Battery(
    "Patriot",
    dtc.PATRIOT,
    _from_template(VehicleTemplate.USA.patriot_site, "USA", "Patriot"),
    AirDefence.M1097_Avenger,
    "Avenger",
    footprint_m=600.0,
)
NASAMS = Battery(
    "NASAMS",
    dtc.NASAMS,
    _from_builder(ad.build_nasams_site),
    AirDefence.M1097_Avenger,
    "Avenger",
)

# Red. The Tunguska is the Avenger's counterpart for the same reason — gun and
# missile on one self-cueing vehicle, so a section holds the overhead without a
# separate radar to kill first.
SA_2 = Battery(
    "SA-2",
    dtc.SA_2,
    _from_builder(ad.build_sa2_site),
    AirDefence.X_2S6_Tunguska,
    "SA-19",
    footprint_m=500.0,
)
SA_3 = Battery(
    "SA-3",
    dtc.SA_3,
    _from_builder(ad.build_sa3_site),
    AirDefence.X_2S6_Tunguska,
    "SA-19",
)
SA_10 = Battery(
    "SA-10",
    dtc.SA_10,
    _from_template(VehicleTemplate.Russia.sa10_site, "Russia", "SA-10"),
    AirDefence.X_2S6_Tunguska,
    "SA-19",
    footprint_m=500.0,
)


@dataclass(frozen=True)
class Sanctuary:
    """A defended field: where to run to, how far the cover reaches, what holds it.

    `radius_m` is the **area** battery's envelope, and it is the only number the
    briefing may quote as cover — the point-defence sections are a few kilometres
    of last-ditch, and claiming their reach as a refuge would be a lie the player
    finds out about at 4 km.
    """

    #: The field this covers.
    airport: "Airport"
    #: What the radio, the map and the group names call it — a callsign.
    callsign: str
    battery: Battery
    #: Where the area battery actually is, which is what the ring is drawn on.
    center: "Point"
    radius_m: float
    #: True for the red side: drawn as an estimate, never as an umbrella.
    enemy: bool
    #: What the map, the cartridge and the kneeboard call this site. Only an
    #: enemy sanctuary uses it — see `label`.
    threat_label: str
    #: A race-track deep inside the envelope. `None` on an enemy field — we do
    #: not brief the other side's recovery pattern, and inventing one would put
    #: a friendly cyan line over a Russian airbase — and `None` on a divert,
    #: which is somewhere you land rather than somewhere you hold.
    marshal: Optional[tuple["Point", "Point"]]
    #: A field the flight does not otherwise use. It gets a labelled position and
    #: no holding pattern; the primary field gets the reverse. See `draw`.
    divert: bool = False
    #: Other fields inside the envelope — briefed alternates, in order.
    alternates: tuple["Airport", ...] = ()
    groups: tuple[VehicleGroup, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """The F10 ring's text, as the map shows it.

        The friendly form names the callsign and states the reach, because the
        reach is the whole message. The enemy form is the mission's own
        `label` and states no figure: `PlanOverlay.threat` appends its own
        "(est.)" or "(approx.)" and coarsens the radius, so a number here would
        be a precise claim inside an imprecise drawing. Keep it inside twenty
        characters — that is the kneeboard threat block's `THREAT` column, and a
        cell over it pushes the rest of the row right.
        """
        if self.enemy:
            return self.threat_label
        return (
            f"{self.callsign} — {self.battery.name} umbrella, "
            f"{self.radius_m / 1000:.0f} km"
        )

    def covers(self, point: "Point") -> bool:
        """Is `point` inside the area battery's envelope?"""
        return self.center.distance_to_point(point) <= self.radius_m

    def draw(self, plan: "PlanOverlay") -> list[dtc.ThreatPoint]:
        """Paint this sanctuary on the F10 plan; return what the cockpit gets.

        Friendly: a precise umbrella, the marshal leg and the field, and an
        empty list — our own battery is not a pre-planned threat and spending a
        cartridge slot on it would cost the mission a real one.

        Enemy: one estimated threat ring through the same reveal policy as every
        other red site, and the pre-planned threat point that goes with it. Feed
        the return straight into the mission's `_load_cartridge` list.

        Call this **early** in a mission's `_draw_plan` either way. `core/dtc.py`
        fills the cartridge's twenty-five navigation steerpoints in draw order
        after the flight's own route, and the marshal point is the one mark on
        the plan a pilot might need with the aircraft already broken — it should
        not be the mark that loses a budget fight to a tanker track.
        """
        if self.enemy:
            estimate = plan.threat(
                self.center,
                radius=self.radius_m,
                label=self.label,
                icon=StandardIcon.AirDefense,
            )
            return dtc.briefed(estimate, self.battery.system, label=self.label)
        plan.umbrella(self.center, self.radius_m, self.label)
        # What a field is worth in the cartridge depends on whether the flight
        # was going there anyway, and the two cases want opposite things.
        #
        # The **primary** field is already the flight's own take-off and landing
        # waypoint — on the route, on the HSD, in the route card's first and last
        # rows — so a mark on it would spend one of twenty-five navigation slots
        # restating it, and on `daryal_run`'s twenty-one-waypoint route that is
        # the slot the marshal leg needs. What it does add is the hold.
        #
        # A **divert** is the mirror image: the flight has no waypoint anywhere
        # near it, so its position is the whole point, and nobody diverts in
        # order to orbit — you are going there because you cannot get home.
        if self.divert:
            plan.waypoint_label(
                self.airport.position, f"{self.airport.name} — divert ({self.callsign})"
            )
        elif self.marshal is not None:
            plan.orbit(*self.marshal, f"{self.callsign} MARSHAL")
        for alt in self.alternates:
            plan.waypoint_label(alt.position, f"{alt.name} — alternate, under cover")
        return []

    def zone(self, m: "Mission") -> "TriggerZoneCircular":
        """The envelope as a hidden trigger zone, for a mission that wants one.

        Nothing here uses it. It is exposed because "the player made it home" is
        a condition a mission may legitimately fire an end-of-sortie call on, and
        re-deriving the radius at the trigger site is how the map and the trigger
        drift apart.
        """
        return m.triggers.add_triggerzone(
            position=self.center,
            radius=int(self.radius_m),
            hidden=True,
            name=f"{self.callsign} umbrella",
        )

    def remarks(self) -> list[str]:
        """The comms-card REMARKS lines for this sanctuary.

        Empty for an enemy field: the route card's threat block already prints
        its coordinates, reach and ceiling off the same `dtc` point `draw`
        returned, and a second prose copy is exactly the stale duplication
        `core/kneeboard` keeps remarks short to avoid.

        For our own, prose is all that is left — the ring is on the map and the
        marshal point is in the DED, so what neither carries is that the cover
        is real, whose it is and which runway sits under it.
        """
        if self.enemy:
            return []
        # One line each, and that is a size budget rather than a style note: the
        # comms card wraps at `kneeboard.page.COLUMNS` (98) with a two-space
        # continuation indent, so a remark that runs over costs two lines of the
        # block instead of one — and a mission with a primary field, a divert and
        # a JTAC carries five of these. The first version of each of these lines
        # was ~135 characters and doubled the densest REMARKS block in the
        # project for nothing anybody needed said at that length.
        where = (
            f"{self.callsign}: {self.battery.name} at {self.airport.name}, "
            f"{self.radius_m / 1000:.0f} km."
        )
        # A divert is somewhere you land, not somewhere you stop being shot at on
        # the way to a decision — telling a pilot to "run for it rather than
        # fighting" would be advice about a field he only reaches once the choice
        # has been made for him.
        lines = [
            f"{where} Take it if you cannot make your own field."
            if self.divert
            else f"{where} Hit, out of missiles or out of fuel — get inside that ring."
        ]
        if self.alternates:
            names = ", ".join(a.name for a in self.alternates)
            lines.append(f"{self.callsign} also covers {names} — divert either way.")
        return lines


def build_sanctuary(
    m: "Mission",
    country: "Country",
    airport: "Airport",
    *,
    callsign: str,
    facing: "Point",
    battery: Battery,
    enemy: bool = False,
    divert: bool = False,
    label: Optional[str] = None,
    keep_clear: Sequence["Point"] = (),
    clearance_m: float = 15_000.0,
    point_defence: int = 3,
    alternates: Sequence["Airport"] = (),
    skill: Skill = Skill.High,
    overlay: "MapOverlay | None" = None,
    terrain: "Terrain | None" = None,
) -> Sanctuary:
    """Emplace the air defence that makes `airport` a place worth running to.

    `facing` is the threat axis — normally the AO, or whichever field the
    opposing fighters come off. The area battery goes on that side of the field
    and the marshal leg on the other, so the pattern a damaged jet flies is not
    the one the pursuit is coming down.

    `keep_clear` is the invariant, and the reason this function can raise: every
    point in it must stay outside the envelope by `clearance_m`. Pass the AO, the
    front line, any site the mission wants left live, and any station a flight
    holds on the wrong side of the line. A `ValueError` here means the chosen
    system is too big for the geometry, not that the mission is wrong — drop to a
    shorter-ranged `Battery` (`NASAMS` over `HAWK` over `PATRIOT`; `SA_3` over
    `SA_2` over `SA_10`).

    On the **red** side, `keep_clear` must include the objective. That is the one
    failure this cannot detect for you and the one that matters most: a ring on
    the enemy field that also reaches the target quietly rewrites the mission the
    player was briefed for. Note that the objective is often *on* the enemy field
    (`eastern_shield`'s depot is the Kuweires apron, `idlib_gauntlet`'s convoy
    off-loads 4 km from Taftanaz), and then no system fits — put the sanctuary on
    the field the fighters recover to instead, which is the one it is for.

    `divert` marks a field the flight does not launch from or recover to — a
    forward strip it can reach when home is too far. It changes what reaches the
    cockpit rather than what gets built: the field's own position instead of a
    holding pattern, for the reasons in `Sanctuary.draw`.

    `label` is what an **enemy** sanctuary is called on the map, in the
    cartridge and on the kneeboard, and it should be what the briefing prose
    calls it — the same rule as `dtc.briefed`'s `label`. Twenty characters is the
    budget. It is unused on our own side, where the callsign carries the name.

    `point_defence` sections sit on the field itself. They are what covers the
    overhead, where the area battery's own minimum range and the terrain mask
    leave a hole, and they are the reason the last four kilometres of a
    straight-in are not free for a chasing fighter.
    """
    axis = airport.position.heading_between_point(facing)
    # Emplace before checking: the position decides the clearance, and a coastal
    # field can move the battery kilometres closer to the runway.
    center = _emplace(airport, axis, overlay, callsign)
    radius = battery.radius_m
    _check_clearance(callsign, center, radius, keep_clear, clearance_m)

    area = battery.build(
        m, country, center, axis, skill=skill, overlay=overlay, terrain=terrain
    )
    # Named after the callsign so a `dcs.log` line, an IADS trace and the F10
    # label all say the same word. The template builders name their group after
    # the system, which puts three "SA-3 site"s in a mission that has three.
    area.name = f"{callsign} {battery.name}"
    ad.disperse_site(
        area, radius_m=battery.footprint_m, overlay=overlay, terrain=terrain
    )
    guns = _spawn_point_defence(
        m,
        country,
        airport,
        callsign=callsign,
        battery=battery,
        count=point_defence,
        skill=skill,
        overlay=overlay,
        terrain=terrain,
    )

    inside = [a for a in alternates if center.distance_to_point(a.position) <= radius]
    for a in alternates:
        if a not in inside:
            log.warning(
                "briefed alternate is outside the umbrella it is briefed under",
                sanctuary=callsign,
                alternate=a.name,
                km=round(center.distance_to_point(a.position) / 1000),
                envelope_km=round(radius / 1000),
            )

    sanctuary = Sanctuary(
        airport=airport,
        callsign=callsign,
        battery=battery,
        center=center,
        radius_m=radius,
        enemy=enemy,
        divert=divert,
        threat_label=label or f"{battery.name} {airport.name}",
        marshal=(
            None
            if enemy or divert
            else _marshal_leg(airport, axis, center=center, radius_m=radius)
        ),
        alternates=tuple(inside),
        groups=(area, *guns),
    )
    log.debug(
        "built sanctuary",
        callsign=callsign,
        field=airport.name,
        battery=battery.name,
        enemy=enemy,
        envelope_km=round(radius / 1000),
        point_defence=len(guns),
        alternates=[a.name for a in inside],
    )
    return sanctuary


def _emplace(
    airport: "Airport",
    axis: float,
    overlay: "MapOverlay | None",
    callsign: str,
) -> "Point":
    """The furthest point up the threat axis that is actually dry, buildable land.

    Doctrine wants the battery between the field and the threat, so the search
    walks *in* along that bearing rather than around it — see
    `_BATTERY_OFFSET_STEPS_M` for why sideways would be worse. With no overlay
    there is nothing to test against and the doctrinal offset stands, which is
    the same degradation as everywhere else in the project: the terrain checks
    are an improvement on a build, never a requirement for one.
    """
    if overlay is None:
        return airport.position.point_from_heading(axis, _BATTERY_OFFSET_M)
    for distance in _BATTERY_OFFSET_STEPS_M:
        candidate = airport.position.point_from_heading(axis, distance)
        if overlay.vegetation_at(candidate) not in NO_FOREST:
            if distance < _BATTERY_OFFSET_M:
                log.debug(
                    "sanctuary battery pulled back toward the field to find land",
                    sanctuary=callsign,
                    field=airport.name,
                    wanted_m=_BATTERY_OFFSET_M,
                    emplaced_m=distance,
                )
            return candidate
    log.warning(
        "no dry ground up the threat axis, siting the battery on the field itself",
        sanctuary=callsign,
        field=airport.name,
    )
    return airport.position


def _check_clearance(
    callsign: str,
    center: "Point",
    radius: float,
    keep_clear: Sequence["Point"],
    clearance_m: float,
) -> None:
    """Refuse an envelope that reaches into the mission.

    A hard failure rather than a warning on purpose. An area SAM that covers the
    AO does not make the mission easier by a measurable amount, it deletes it —
    the belts the player was briefed to work around get shot from the other side
    of the map by an asset nobody planned the sortie against — and it does that
    silently, because the only symptom in the built `.miz` is a circle on the F10
    map that happens to be too big.
    """
    for point in keep_clear:
        gap = center.distance_to_point(point) - radius
        if gap < clearance_m:
            raise ValueError(
                f"{callsign}'s {radius / 1000:.0f} km envelope comes within "
                f"{gap / 1000:.1f} km of a point the mission needs left in play "
                f"(wanted {clearance_m / 1000:.0f} km). Use a shorter-ranged "
                f"Battery, move the sanctuary to a field further back, or drop "
                f"the point from keep_clear if it really is meant to be inside "
                f"the envelope."
            )


def _spawn_point_defence(
    m: "Mission",
    country: "Country",
    airport: "Airport",
    *,
    callsign: str,
    battery: Battery,
    count: int,
    skill: Skill,
    overlay: "MapOverlay | None",
    terrain: "Terrain | None",
) -> tuple[VehicleGroup, ...]:
    """Self-cueing SHORAD sections around the field's own reference point.

    One group per section rather than one group of `count`: a DCS group holds
    fire and reacts as a unit, so three sections spread round a runway are three
    independent engagements, which is the entire reason for siting them apart.
    """
    groups = []
    for i in range(count):
        bearing = i * 360.0 / max(count, 1)
        pos = airport.position.point_from_heading(bearing, _POINT_DEFENCE_RING_M)
        grp = m.vehicle_group(
            country,
            f"{callsign} {battery.point_defence_name}-{i + 1}",
            battery.point_defence,
            position=pos,
            heading=int(bearing),
        )
        mission_kit.set_skill(grp, skill)
        if overlay is not None and terrain is not None:
            # `disperse_site` returns early on a one-unit group, so it would
            # skip the snap with it — and an airfield boundary is exactly where
            # a treeline is.
            snap_units_clear(overlay, terrain, grp)
        groups.append(grp)
    return tuple(groups)


def _marshal_leg(
    airport: "Airport", threat_axis: float, *, center: "Point", radius_m: float
) -> tuple["Point", "Point"]:
    """A race-track abeam the field, on the quarter away from the threat.

    Off the extended centreline, so a flight holding here is neither in the
    approach path of whatever else is recovering nor in the first place a
    pursuing fighter arrives. The leg runs across the threat axis rather than
    along it: a jet in the pattern is then always turning back toward the field
    rather than away from it.

    Sized to fit `radius_m` — see `_MARSHAL_STANDOFF_M`. It halves until the
    furthest end of the race-track is within `_MARSHAL_FIT` of the envelope,
    measured from the battery rather than from the runway, because the battery is
    what the envelope is centred on and it is offset up the threat axis. A
    degenerate leg is still returned at the floor: a two-kilometre pattern over
    the field is a poor hold and an honest one, and drawing nothing would leave
    the mission with no marshal point at all.
    """
    standoff = _MARSHAL_STANDOFF_M
    while standoff > 2_000.0:
        legs = _leg_at(airport, threat_axis, standoff)
        if max(center.distance_to_point(p) for p in legs) <= radius_m * _MARSHAL_FIT:
            return legs
        standoff /= 2.0
    return _leg_at(airport, threat_axis, standoff)


def _leg_at(
    airport: "Airport", threat_axis: float, standoff: float
) -> tuple["Point", "Point"]:
    hub = airport.position.point_from_heading(threat_axis + 180.0, standoff)
    return (
        hub.point_from_heading(threat_axis + 90.0, standoff / 2.0),
        hub.point_from_heading(threat_axis - 90.0, standoff / 2.0),
    )


def remark_all(m: "Mission", *sanctuaries: Sanctuary) -> None:
    """Put every friendly sanctuary's REMARKS lines on the comms card.

    Enemy sanctuaries contribute nothing and may be passed anyway, so a mission
    can hand this whatever `_spawn_sanctuaries` returned without filtering.
    """
    from dcs_mission_creator.core import kneeboard

    for s in sanctuaries:
        for line in s.remarks():
            kneeboard.remark(m, line)


def announce(
    m: "Mission",
    sanctuary: Sanctuary,
    *,
    at_seconds: int,
    voice: "VoiceSynth | None" = None,
    controller: str = "Magic",
    comment: str | None = None,
) -> None:
    """Read the umbrella out on the clock, once, early enough to be heard.

    Every mission wrote this call and every one wrote it the same way, down to
    the controller and the comment string; what actually differed was
    `at_seconds`, which is a pacing decision and stays a required argument.

    It is not decoration, and that is the reason it exists at all rather than
    being left to the F10 map: a cyan ring reads as scenery, and nobody opens
    the map again after push. Same argument as `core/jtac`'s `push_at_s`.

    `controller` is who says it — the AWACS in every mission here so far, but a
    package with a different agency should say so — and `comment` is the trigger
    label in the editor, which defaults to naming the sanctuary.
    """
    from dcs_mission_creator.core import triggers as mission_triggers

    mission_triggers.checkin(
        m,
        at_seconds=at_seconds,
        comment=comment or f"{sanctuary.callsign} umbrella check-in",
        voice=voice,
        text=checkin_text(sanctuary, controller=controller),
    )


def checkin_text(sanctuary: Sanctuary, *, controller: str) -> str:
    """The words a controller uses to hand the player the sanctuary, once.

    Spoken as well as printed (`core/triggers.checkin`), because a refuge the
    player never learns about is not one — the same argument as
    `core/jtac.arm_jtac_coords`'s `push_at_s`. The reach is in nautical miles:
    the number is for a pilot reading an HSD range ring, not for a briefing
    table.
    """
    if sanctuary.enemy:
        raise ValueError(
            f"{sanctuary.callsign} is an enemy sanctuary — there is no friendly "
            f"controller to read it out, and the player learns about it from the "
            f"briefing and the drawn threat ring like any other red site."
        )
    nm = sanctuary.radius_m / 1852.0
    return (
        f"{controller}: {sanctuary.callsign} is a {sanctuary.battery.name} battery "
        f"at {sanctuary.airport.name}, covering {nm:.0f} miles. If you are hit, "
        f"out of missiles or out of fuel, come inside that ring — "
        f"{sanctuary.callsign} MARSHAL is in the DED. Nobody follows you in there."
    )


__all__ = [
    "Battery",
    "HAWK",
    "NASAMS",
    "PATRIOT",
    "SA_2",
    "SA_3",
    "SA_10",
    "Sanctuary",
    "build_sanctuary",
    "checkin_text",
    "remark_all",
]
