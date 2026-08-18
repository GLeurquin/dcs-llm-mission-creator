# Coastal Cover

**Theater:** Caucasus
**Date / time:** 15 May 2026, 10:00 local
**Player aircraft:** F-16C-50 (`Dodge`), Batumi, hot ramp
**Players:** 1 coop slot(s)
**Difficulty:** trained — experienced MiG-29S pair, current-generation
missiles, GCI vectoring, SHORAD over the target, AWACS support, no tanker
**Expected sortie length:** ~50 minutes

## Situation

A Reaper feed at first light watched a Russian mechanised column form up in
the Inguri valley and start south on the valley road toward Senaki. USAF
A-10s (`Hawg 1-2`) out of Kutaisi are fragged against it. Russian fighters at
Sukhumi-Babushara went to alert on the same warning — expect them airborne
once the package commits.

## Mission

Push north as `Dodge` flight along a terrain-masked ingress corridor, take
station over the AO, sanitize the airspace ahead of `Hawg`'s run, and engage
Russian fighters before they get a shot on the strike.

## Package

| Callsign | Type     | Base    | Role                         |
|----------|----------|---------|------------------------------|
| Dodge    | F-16C-50 | Batumi  | Player CAP / escort          |
| Hawg 1-2 | A-10C    | Kutaisi | Strike on convoy lead        |
| Eagle 1-2| F-15C    | Batumi  | High cover CAP (overlay)     |
| Magic    | E-3A     | Batumi  | AWACS, Black Sea track       |

No tanker — F-16C internal fuel covers the sortie with a ~10 min margin.

## Intelligence

- **Air:** Sukhumi-Babushara holds a MiG-29S pair on alert, current-generation
  missiles, flown by an experienced crew. They will come once we are committed
  over the valley.
- **EWR:** A Rivet Joint track overnight fixed early-warning radars along the
  Russian frontier. Assume the pair is vectored onto you from the moment you
  cross the coast.
- **SAM:** The Reaper feed showed a tracked SHORAD launcher moving onto high
  ground overlooking the road — SA-13 class, IR-guided, short reach. Stay
  above 4000 m AGL over the target box and it cannot reach you.
- **AAA:** Gun vehicles ride with the column, and the same imagery showed
  dug-in guns on the hills either side of the valley road.
- **Land reserve:** Partner-force reporting puts a small armoured reserve
  laagered in the treeline behind the column, held back to push through if the
  lead elements are hit. Unconfirmed.

## ROE

- Hold fire on civilian / neutral contacts.
- Cleared to engage any Russian aircraft entering the AO.
- Do not overfly the convoy below 4000 m AGL.
- Bingo fuel: 2500 lb. RTB Batumi (divert: Kutaisi).

## Navigation

- Bullseye (own side): `-291014, 617414` (DCS world m)
- AO center: ~18 km north-northeast of Senaki.
- PUSH waypoint: 25 km north of Batumi (corridor IP).
- Your route is a terrain-masked corridor that keeps ridgelines between you
  and the reported launcher and radar positions for as long as it can.

## Frequencies

- Magic AWACS: 251.000 AM
- Batumi tower: per kneeboard

## Weather

Spring scattered cumulus, light NW wind, 18 °C. QNH 760 mmHg. Visibility
80 km. Scattered layer at 2400 m, 600 m thick.

## Win / loss conditions

- **Success:** the Russian column is broken up on the valley road and never
  reaches Senaki.
- **Failure:** `Hawg` is shot down with the column still rolling.

## Re-generate

```bash
uv run dcs-mission-creator generate coastal_cover --players 1
```
