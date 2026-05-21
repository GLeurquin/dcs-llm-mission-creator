"""Multi-placement scene composition helpers.

Each method consumes a `MapOverlay` and returns mission-ready point lists. The
helpers carry mission-archetype logic so callers (mission builders) avoid
duplicating it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from dcs.mapping import Point

from dcs_mission_creator.map_overlay.placement import Placement, Vegetation
from dcs_mission_creator.map_overlay.query import MapOverlay


@dataclass
class ConvoyRoute:
    """A sequence of OnRoad waypoints suitable for `add_waypoint(...)`."""

    waypoints: list[Point]
    total_length_m: float


def _distance_m(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _offset_point(origin: Point, bearing_deg: float, distance_m: float) -> Point:
    """Return point at `distance_m` from origin along `bearing_deg` (0=N, 90=E)."""
    rad = math.radians(bearing_deg)
    return Point(
        origin.x + math.cos(rad) * distance_m,
        origin.y + math.sin(rad) * distance_m,
        origin._terrain,
    )


def _bearing(a: Point, b: Point) -> float:
    """Heading from a to b in degrees (0=N, 90=E)."""
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 360.0


@dataclass
class Farp:
    """Helo forward-arming pad: cluster center + four landing spots."""

    center: Point
    pads: list[Point]


@dataclass
class CsarSite:
    """Downed pilot location with a nearby flat extract LZ."""

    pilot: Point
    lz: Point


@dataclass
class CarrierGroup:
    """Carrier + escort screen + optional forward picket."""

    carrier: Point
    escorts: list[Point]
    picket: Point | None = None


@dataclass
class AirfieldRingDefense:
    """Concentric airfield defense layout."""

    shorad: list[Point]
    aaa: list[Point]
    long_sam: list[Point]


@dataclass
class TacticalScene:
    overlay: MapOverlay

    # ----------------------------------------------------------------- convoy
    def place_convoy_route(self, origin: Point, destination: Point) -> ConvoyRoute:
        """Snap both endpoints to roads. DCS engine routes between them.

        Caller threads each waypoint into
        `VehicleGroup.add_waypoint(p, move_formation=PointAction.OnRoad)`.
        For complex routes the caller can call this multiple times to insert
        intermediate snapped waypoints.
        """
        snapped_origin = self.overlay.find_road_spawn(origin, radius_m=5_000)
        snapped_dest = self.overlay.find_road_spawn(destination, radius_m=5_000)
        return ConvoyRoute(
            waypoints=[snapped_origin, snapped_dest],
            total_length_m=_distance_m(snapped_origin, snapped_dest),
        )

    # ----------------------------------------------------------------- ambush
    def place_ambush_on_route(
        self,
        route: ConvoyRoute,
        phase: float = 0.5,
        concealment: Literal["treeline", "ridge", "village"] = "treeline",
        hidden_from: Point | None = None,
    ) -> Point:
        """Pick an ambush position along the convoy route.

        `phase` ∈ [0, 1] is the fractional position along the route; 0.5 is
        the midpoint. `concealment` chooses the placement profile:
            - "treeline": near forest edge, light forest OK
            - "ridge":    on a hilltop with prominence
            - "village":  in built-up outskirts
        `hidden_from` adds a `no_line_of_sight_to` constraint.
        """
        if not 0.0 <= phase <= 1.0:
            raise ValueError(f"phase must be in [0,1], got {phase}")
        if len(route.waypoints) < 2:
            raise ValueError("route must have >= 2 waypoints")
        # Approximate phase position by linear interpolation between the two
        # endpoint waypoints (DCS engine routes between them; we use straight
        # line as a proxy for the actual road).
        a, b = route.waypoints[0], route.waypoints[-1]
        target = Point(
            a.x + phase * (b.x - a.x),
            a.y + phase * (b.y - a.y),
            a._terrain,
        )

        if concealment == "treeline":
            require = Placement.near_treeline(
                within_m=80,
                light_forest_ok=True,
                near_road_m=200,
                max_slope_deg=20,
                not_in_built_up=True,
            )
        elif concealment == "ridge":
            require = Placement.on_hilltop(
                min_prominence_m=30,
                max_slope_deg=20,
                near_road_m=300,
                not_in_built_up=True,
            )
        elif concealment == "village":
            require = Placement(
                near_road_m=150,
                max_slope_deg=15,
                not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
            )
        else:
            raise ValueError(f"unknown concealment {concealment!r}")

        if hidden_from is not None:
            require = require.merged_with(no_line_of_sight_to=(hidden_from,))

        spots = self.overlay.find_placement(target, radius_m=2_000, require=require)
        if not spots:
            raise LookupError(f"no ambush spot near phase={phase} with {concealment!r}")
        return spots[0]

    # ------------------------------------------------------------- sam (defending)
    def place_sam_defending(
        self,
        asset: Point,
        threat_axis_deg: float,
        envelope_radius_m: float,
        min_prominence_m: float = 30.0,
    ) -> Point:
        """Place a SAM within `envelope_radius_m` of `asset`, sector-aware.

        Threat axis is the direction the threat comes from (degrees, 0=N).
        SAM is placed in a ±90° arc centered on the threat axis (so it covers
        the ingress corridor).
        """
        h_min = (threat_axis_deg - 90) % 360
        h_max = (threat_axis_deg + 90) % 360
        require = Placement(
            max_slope_deg=10,
            not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
            not_in_built_up=True,
            min_relative_height_m=min_prominence_m,
            relative_height_radius_m=2_000.0,
            in_sector_from=(asset, h_min, h_max),
            max_distance_to=((asset, envelope_radius_m),),
            line_of_sight_to=(asset,),
            min_distance_to=((asset, 1_000.0),),  # don't sit on top of it
        )
        spots = self.overlay.find_placement(
            asset, radius_m=envelope_radius_m, require=require
        )
        if not spots:
            raise LookupError(
                f"no SAM spot within {envelope_radius_m:.0f}m of asset; "
                "relax min_prominence_m or envelope"
            )
        return spots[0]

    # --------------------------------------------------------------- ewr chain
    def place_ewr_chain(
        self,
        frontier_polyline: list[Point],
        count: int = 3,
        min_spacing_m: float = 30_000.0,
        min_elevation_m: float = 400.0,
    ) -> list[Point]:
        """Place `count` EWRs along `frontier_polyline`, high ground + LOS forward.

        Frontier is treated as a string of anchor points; each EWR is placed
        within 10 km of one anchor, with minimum spacing between EWRs enforced
        by rejection sampling.
        """
        placed: list[Point] = []
        # Distribute anchors along the polyline by phase 0, 1/count, 2/count, ...
        n_anchors = max(count, 2)
        anchors: list[Point] = []
        if len(frontier_polyline) == 1:
            anchors = [frontier_polyline[0]] * count
        else:
            for i in range(n_anchors):
                t = i / (n_anchors - 1)
                # Project t onto polyline by cumulative length
                cumlen = [0.0]
                for j in range(1, len(frontier_polyline)):
                    cumlen.append(
                        cumlen[-1]
                        + _distance_m(frontier_polyline[j - 1], frontier_polyline[j])
                    )
                total = cumlen[-1]
                target_len = t * total
                # Find segment containing target_len
                for j in range(1, len(cumlen)):
                    if cumlen[j] >= target_len:
                        seg_t = (target_len - cumlen[j - 1]) / max(
                            1e-6, cumlen[j] - cumlen[j - 1]
                        )
                        a, b = frontier_polyline[j - 1], frontier_polyline[j]
                        anchors.append(
                            Point(
                                a.x + seg_t * (b.x - a.x),
                                a.y + seg_t * (b.y - a.y),
                                a._terrain,
                            )
                        )
                        break

        for anchor in anchors[:count]:
            min_distance_to = tuple((p, min_spacing_m) for p in placed)
            require = Placement(
                max_slope_deg=25,
                not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
                not_in_built_up=True,
                min_elevation_m=min_elevation_m,
                min_relative_height_m=50.0,
                relative_height_radius_m=3_000.0,
                min_distance_to=min_distance_to,
            )
            spots = self.overlay.find_placement(
                anchor, radius_m=10_000, require=require
            )
            if spots:
                placed.append(spots[0])
        if len(placed) < count:
            raise LookupError(
                f"could only place {len(placed)}/{count} EWRs; widen filters"
            )
        return placed

    # --------------------------------------------------------- carrier group
    def place_carrier_group(
        self,
        mission_ao: Point,
        max_distance_km: float = 150.0,
        min_distance_to_shore_m: float = 30_000.0,
    ) -> list[Point]:
        """Pick one carrier-group anchor in deep water near the AO.

        Returns a single-element list (the carrier position). Caller spawns
        the escort ships at fixed offsets around it.
        """
        require = Placement(
            not_in=(Vegetation.NONE, Vegetation.LIGHT_FOREST, Vegetation.DENSE_FOREST),
            # vegetation==WATER required (negate "not_in" by listing all land classes)
            near_water_m=0.0,  # must be inside water cell
            min_distance_to_road_m=min_distance_to_shore_m,
            # "Far from shore" approximated via min distance from any road.
        )
        spots = self.overlay.find_placement(
            mission_ao, radius_m=max_distance_km * 1_000, require=require
        )
        if not spots:
            raise LookupError("no deep-water spot found near AO")
        return [spots[0]]

    # ------------------------------------------------------------ aaa overwatch
    def place_aaa_overwatch(
        self, defended_axis: list[Point], count: int = 3
    ) -> list[Point]:
        """Place AAA on hilltops with LOS to the defended axis.

        `defended_axis` is a sequence of waypoints (e.g. an ingress corridor).
        AAA are scattered around the corridor on prominent terrain.
        """
        if not defended_axis:
            return []
        placed: list[Point] = []
        for i in range(count):
            anchor = defended_axis[i % len(defended_axis)]
            require = Placement.on_hilltop(
                min_prominence_m=40,
                max_slope_deg=20,
                line_of_sight_to=(defended_axis[i % len(defended_axis)],),
                not_in_built_up=True,
                min_distance_to=tuple((p, 2_000.0) for p in placed),
            )
            spots = self.overlay.find_placement(anchor, radius_m=5_000, require=require)
            if spots:
                placed.append(spots[0])
        return placed

    # ------------------------------------------------------------------ farp
    def place_farp(
        self,
        near: Point,
        radius_m: float = 20_000.0,
        threat_axis_deg: float | None = None,
        min_distance_to_threats: tuple[tuple[Point, float], ...] = (),
    ) -> Farp:
        """Concealed helo FARP: flat clearing at treeline edge, near road.

        Optional `threat_axis_deg` keeps the FARP hidden from that bearing
        via no_line_of_sight to a probe point 30 km along the axis.
        """
        require = Placement.near_treeline(
            within_m=120,
            light_forest_ok=True,
            near_road_m=2_000,
            max_slope_deg=3,
            not_in_built_up=True,
            min_distance_to=min_distance_to_threats,
        )
        if threat_axis_deg is not None:
            probe = _offset_point(near, threat_axis_deg, 30_000.0)
            require = require.merged_with(no_line_of_sight_to=(probe,))
        spots = self.overlay.find_placement(near, radius_m=radius_m, require=require)
        if not spots:
            raise LookupError("no FARP spot — relax slope or treeline radius")
        c = spots[0]
        pads = [
            Point(c.x + 60, c.y, c._terrain),
            Point(c.x - 60, c.y, c._terrain),
            Point(c.x, c.y + 60, c._terrain),
            Point(c.x, c.y - 60, c._terrain),
        ]
        return Farp(center=c, pads=pads)

    # ------------------------------------------------------------------- csar
    def place_csar_site(
        self,
        inside_enemy_area: Point,
        search_radius_m: float = 15_000.0,
        max_lz_offset_m: float = 600.0,
    ) -> CsarSite:
        """Downed pilot in concealment with a nearby flat extract LZ."""
        pilot_req = Placement.near_treeline(
            within_m=40,
            light_forest_ok=True,
            max_slope_deg=20,
            not_in_built_up=True,
        )
        pilots = self.overlay.find_placement(
            inside_enemy_area, radius_m=search_radius_m, require=pilot_req
        )
        if not pilots:
            raise LookupError("no concealed pilot spot")
        pilot = pilots[0]
        lz_req = Placement(
            max_slope_deg=5,
            not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST, Vegetation.LIGHT_FOREST),
            not_in_built_up=True,
            max_distance_to=((pilot, max_lz_offset_m),),
            min_distance_to=((pilot, 150.0),),
        )
        lzs = self.overlay.find_placement(
            pilot, radius_m=max_lz_offset_m, require=lz_req
        )
        if not lzs:
            raise LookupError("pilot found but no extract LZ within reach")
        return CsarSite(pilot=pilot, lz=lzs[0])

    # ------------------------------------------------------------------- hot lz
    def place_hot_lz_chain(
        self,
        route: list[Point],
        count: int,
        hidden_from: Point | None = None,
    ) -> list[Point]:
        """N flat LZs along an ingress route, optionally hidden from a threat point."""
        if count <= 0 or len(route) < 2:
            return []
        placed: list[Point] = []
        a, b = route[0], route[-1]
        for i in range(count):
            t = (i + 0.5) / count
            anchor = Point(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y), a._terrain)
            require = Placement(
                max_slope_deg=5,
                not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
                not_in_built_up=True,
                near_road_m=500,
                min_distance_to=tuple((p, 1_500.0) for p in placed),
            )
            if hidden_from is not None:
                require = require.merged_with(no_line_of_sight_to=(hidden_from,))
            spots = self.overlay.find_placement(anchor, radius_m=3_000, require=require)
            if spots:
                placed.append(spots[0])
        return placed

    # --------------------------------------------------------------- frontline
    def place_frontline(
        self,
        blue_anchor: Point,
        red_anchor: Point,
        waypoints: int = 5,
        meander_m: float = 8_000.0,
    ) -> list[Point]:
        """Meandering FLOT polyline between two coalition anchors.

        Picks `waypoints` intermediate points perpendicular-offset from the
        straight line, biased toward prominent terrain.
        """
        a, b = blue_anchor, red_anchor
        pts: list[Point] = [a]
        for i in range(1, waypoints + 1):
            t = i / (waypoints + 1)
            center = Point(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y), a._terrain)
            require = Placement(
                max_slope_deg=35,
                not_in=(Vegetation.WATER,),
                min_relative_height_m=20.0,
                relative_height_radius_m=2_000.0,
            )
            spots = self.overlay.find_placement(
                center, radius_m=meander_m, require=require
            )
            pts.append(spots[0] if spots else center)
        pts.append(b)
        return pts

    # ----------------------------------------------------------- artillery firebase
    def place_artillery_firebase(
        self,
        target: Point,
        max_range_m: float = 20_000.0,
        min_range_m: float = 6_000.0,
        threats: tuple[tuple[Point, float], ...] = (),
    ) -> Point:
        """High ground with LOS to target, road-accessible, outside threat rings."""
        require = Placement.on_hilltop(
            min_prominence_m=30,
            max_slope_deg=15,
            near_road_m=2_500,
            not_in_built_up=True,
            line_of_sight_to=(target,),
            max_distance_to=((target, max_range_m),),
            min_distance_to=((target, min_range_m),) + threats,
        )
        spots = self.overlay.find_placement(
            target, radius_m=max_range_m, require=require
        )
        if not spots:
            raise LookupError("no firebase spot — extend range or relax LOS")
        return spots[0]

    # ------------------------------------------------------------- strike cluster
    def place_strike_cluster(
        self,
        town_center: Point,
        count: int = 4,
        cluster_radius_m: float = 1_500.0,
        min_spacing_m: float = 300.0,
    ) -> list[Point]:
        """N sub-targets inside a built-up area: HQ + fuel + warehouse + barracks."""
        placed: list[Point] = []
        for _ in range(count):
            require = Placement(
                max_slope_deg=20,
                not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
                near_road_m=200,
                min_distance_to=tuple((p, min_spacing_m) for p in placed),
            )
            spots = self.overlay.find_placement(
                town_center, radius_m=cluster_radius_m, require=require
            )
            if spots:
                placed.append(spots[0])
        if len(placed) < count:
            raise LookupError(
                f"placed {len(placed)}/{count} sub-targets; widen cluster_radius"
            )
        return placed

    # ------------------------------------------------------------ bridge chokepoint
    def place_bridge_chokepoint(
        self,
        near: Point,
        radius_m: float = 30_000.0,
        count: int = 1,
    ) -> list[Point]:
        """Road–river crossings: cells within 60 m of both a road and a river."""
        require = Placement(
            near_road_m=60,
            near_water_m=60,
            max_slope_deg=20,
        )
        spots = self.overlay.find_placement(
            near, radius_m=radius_m, require=require, count=count
        )
        if not spots:
            raise LookupError("no road–river crossing nearby")
        return spots

    # ---------------------------------------------------------------- tanker track
    def place_tanker_track(
        self,
        home_base: Point,
        threat_axis: Point,
        standoff_m: float = 80_000.0,
        track_length_m: float = 60_000.0,
        over_water: bool = False,
    ) -> tuple[Point, Point]:
        """Race-track standoff from threat axis, anchored toward friendly base."""
        bearing_threat = _bearing(home_base, threat_axis)
        safe_bearing = (bearing_threat + 180.0) % 360.0
        p1 = _offset_point(home_base, safe_bearing, standoff_m)
        p2 = _offset_point(p1, (safe_bearing + 90.0) % 360.0, track_length_m)
        if over_water:
            req = Placement(near_water_m=0.0, min_distance_to_road_m=10_000.0)
            for i, p in enumerate((p1, p2)):
                spots = self.overlay.find_placement(p, radius_m=20_000, require=req)
                if spots:
                    if i == 0:
                        p1 = spots[0]
                    else:
                        p2 = spots[0]
        return p1, p2

    def place_awacs_track(
        self,
        home_base: Point,
        threat_axis: Point,
        standoff_m: float = 120_000.0,
        track_length_m: float = 80_000.0,
    ) -> tuple[Point, Point]:
        """AWACS race-track: deeper standoff than tanker, no over-water constraint."""
        return self.place_tanker_track(
            home_base,
            threat_axis,
            standoff_m=standoff_m,
            track_length_m=track_length_m,
            over_water=False,
        )

    # ------------------------------------------------------------------ cap station
    def place_cap_station(
        self,
        defended_asset: Point,
        threat_bearing_deg: float,
        forward_distance_m: float = 40_000.0,
        track_length_m: float = 40_000.0,
    ) -> tuple[Point, Point]:
        """Two-point race-track between asset and threat bearing."""
        p1 = _offset_point(defended_asset, threat_bearing_deg, forward_distance_m)
        p2 = _offset_point(p1, (threat_bearing_deg + 90.0) % 360.0, track_length_m)
        return p1, p2

    # -------------------------------------------------------- ingress corridor
    def place_ingress_corridor(
        self,
        ip: Point,
        target: Point,
        threats: tuple[Point, ...],
        waypoints: int = 4,
        leg_search_radius_m: float = 8_000.0,
    ) -> list[Point]:
        """Terrain-masked waypoint chain from IP to target avoiding LOS to threats."""
        if waypoints <= 0:
            return [ip, target]
        pts: list[Point] = [ip]
        for i in range(1, waypoints + 1):
            t = i / (waypoints + 1)
            anchor = Point(
                ip.x + t * (target.x - ip.x),
                ip.y + t * (target.y - ip.y),
                ip._terrain,
            )
            require = Placement(
                max_slope_deg=45,
                not_in=(Vegetation.WATER,),
                no_line_of_sight_to=threats,
            )
            spots = self.overlay.find_placement(
                anchor, radius_m=leg_search_radius_m, require=require
            )
            pts.append(spots[0] if spots else anchor)
        pts.append(target)
        return pts

    # ------------------------------------------------------------ airfield ring
    def place_airfield_ring_defense(
        self,
        airfield: Point,
        threat_axis_deg: float,
        shorad_count: int = 3,
        aaa_count: int = 4,
        long_sam_count: int = 1,
    ) -> AirfieldRingDefense:
        """Concentric defense: SHORAD inner, AAA hilltops, long-range SAM forward."""
        return AirfieldRingDefense(
            shorad=self._ring_shorad(airfield, shorad_count),
            aaa=self._ring_aaa(airfield, aaa_count),
            long_sam=self._ring_long_sam(airfield, threat_axis_deg, long_sam_count),
        )

    def _ring_shorad(self, airfield: Point, count: int) -> list[Point]:
        """SHORAD 0.8–3 km from airfield, flat ground, spaced 400 m apart."""
        placed: list[Point] = []
        for _ in range(count):
            req = Placement(
                max_slope_deg=10,
                not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
                min_distance_to=((airfield, 800.0),)
                + tuple((p, 400.0) for p in placed),
                max_distance_to=((airfield, 3_000.0),),
            )
            spots = self.overlay.find_placement(airfield, radius_m=3_000, require=req)
            if spots:
                placed.append(spots[0])
        return placed

    def _ring_aaa(self, airfield: Point, count: int) -> list[Point]:
        """AAA hilltops 3–8 km from airfield, prominence ≥ 30 m, ≥ 1.5 km apart."""
        placed: list[Point] = []
        for _ in range(count):
            req = Placement.on_hilltop(
                min_prominence_m=30,
                max_slope_deg=20,
                not_in_built_up=True,
                min_distance_to=((airfield, 3_000.0),)
                + tuple((p, 1_500.0) for p in placed),
                max_distance_to=((airfield, 8_000.0),),
            )
            spots = self.overlay.find_placement(airfield, radius_m=8_000, require=req)
            if spots:
                placed.append(spots[0])
        return placed

    def _ring_long_sam(
        self, airfield: Point, threat_axis_deg: float, count: int
    ) -> list[Point]:
        """Long-range SAM on commanding ground in a 120° arc toward threat."""
        placed: list[Point] = []
        h_min = (threat_axis_deg - 60.0) % 360.0
        h_max = (threat_axis_deg + 60.0) % 360.0
        for _ in range(count):
            req = Placement(
                max_slope_deg=10,
                not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
                not_in_built_up=True,
                min_relative_height_m=40.0,
                in_sector_from=(airfield, h_min, h_max),
                min_distance_to=((airfield, 10_000.0),)
                + tuple((p, 5_000.0) for p in placed),
                max_distance_to=((airfield, 20_000.0),),
            )
            spots = self.overlay.find_placement(airfield, radius_m=20_000, require=req)
            if spots:
                placed.append(spots[0])
        return placed

    # --------------------------------------------------------------- sam ambush
    def place_sam_ambush(
        self,
        engagement_zone: Point,
        ingress_ip: Point,
        envelope_radius_m: float = 25_000.0,
    ) -> Point:
        """Pop-up SAM: hidden from ingress IP, LOS only to engagement zone.

        Reverse-slope SEAD trap. The shooter waits behind a ridge until the
        target enters the engagement zone, then unmasks.
        """
        require = Placement(
            max_slope_deg=15,
            not_in=(Vegetation.WATER, Vegetation.DENSE_FOREST),
            not_in_built_up=True,
            line_of_sight_to=(engagement_zone,),
            no_line_of_sight_to=(ingress_ip,),
            max_distance_to=((engagement_zone, envelope_radius_m),),
            min_distance_to=((engagement_zone, 3_000.0),),
        )
        spots = self.overlay.find_placement(
            engagement_zone, radius_m=envelope_radius_m, require=require
        )
        if not spots:
            raise LookupError(
                "no reverse-slope SAM spot — engagement zone may be in open terrain"
            )
        return spots[0]

    # ----------------------------------------------------- carrier group + screen
    def place_carrier_group_with_screen(
        self,
        mission_ao: Point,
        threat_bearing_deg: float,
        max_distance_km: float = 150.0,
        min_distance_to_shore_m: float = 30_000.0,
        escort_count: int = 2,
        picket_distance_m: float = 25_000.0,
    ) -> CarrierGroup:
        """Carrier + DDG screen spread on the threat bearing + forward picket."""
        cv = self.place_carrier_group(
            mission_ao,
            max_distance_km=max_distance_km,
            min_distance_to_shore_m=min_distance_to_shore_m,
        )[0]
        spread = 60.0
        escorts: list[Point] = []
        denom = max(1, escort_count - 1)
        for i in range(escort_count):
            bearing = (threat_bearing_deg - spread / 2.0 + spread * i / denom) % 360.0
            escorts.append(_offset_point(cv, bearing, 8_000.0))
        picket = _offset_point(cv, threat_bearing_deg, picket_distance_m)
        return CarrierGroup(carrier=cv, escorts=escorts, picket=picket)

    # ------------------------------------------------------ surface action group
    def place_surface_action_group(
        self,
        shore_target: Point,
        count: int = 3,
        standoff_m: float = 25_000.0,
        spacing_m: float = 2_000.0,
    ) -> list[Point]:
        """N ships line-abreast offshore, parallel to coast, naval gunfire range."""
        require = Placement(
            near_water_m=0.0,
            min_distance_to_road_m=standoff_m,
            max_distance_to=((shore_target, standoff_m + 20_000.0),),
            min_distance_to=((shore_target, standoff_m),),
        )
        spots = self.overlay.find_placement(
            shore_target, radius_m=standoff_m + 20_000.0, require=require
        )
        if not spots:
            raise LookupError("no offshore line found at requested standoff")
        anchor = spots[0]
        line_bearing = (_bearing(anchor, shore_target) + 90.0) % 360.0
        return [
            _offset_point(anchor, line_bearing, (i - (count - 1) / 2.0) * spacing_m)
            for i in range(count)
        ]

    # --------------------------------------------------------- armored advance
    def place_armored_advance(
        self,
        origin: Point,
        destination: Point,
        columns: int = 3,
        column_spacing_m: float = 4_000.0,
    ) -> list[ConvoyRoute]:
        """N parallel convoy columns on the same axis, perpendicular-offset."""
        perp = (_bearing(origin, destination) + 90.0) % 360.0
        routes: list[ConvoyRoute] = []
        for i in range(columns):
            off = (i - (columns - 1) / 2.0) * column_spacing_m
            o = _offset_point(origin, perp, off)
            d = _offset_point(destination, perp, off)
            try:
                routes.append(self.place_convoy_route(o, d))
            except LookupError:
                continue
        if not routes:
            raise LookupError("no parallel routes found — terrain too constrained")
        return routes

    # ----------------------------------------------------- counterattack reserve
    def place_counterattack_reserve(
        self,
        flot_point: Point,
        rear_bearing_deg: float,
        rear_distance_m: float = 15_000.0,
        search_radius_m: float = 5_000.0,
        hidden_from: Point | None = None,
    ) -> Point:
        """Armor reserve in treeline behind FLOT, road-accessible for push forward."""
        anchor = _offset_point(flot_point, rear_bearing_deg, rear_distance_m)
        require = Placement.near_treeline(
            within_m=100,
            light_forest_ok=True,
            near_road_m=1_500,
            max_slope_deg=15,
            not_in_built_up=True,
        )
        if hidden_from is not None:
            require = require.merged_with(no_line_of_sight_to=(hidden_from,))
        spots = self.overlay.find_placement(
            anchor, radius_m=search_radius_m, require=require
        )
        if not spots:
            raise LookupError("no concealed reserve spot")
        return spots[0]
