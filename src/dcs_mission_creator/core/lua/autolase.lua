-- dcs_mission_creator: a designator already lasing when the flight arrives.
--
-- Rendered by `core/laser.py` into a mission-start DoScript. It fills eight
-- placeholders (named here without their underscores so this comment is not
-- itself substituted): SPOTS is one `{observer=…, target=…, …}` row per
-- designation, VERIFY the seconds between two target-selection passes, DRIFT how
-- far a target may travel before the spot is moved, TICK the floor and CEILING
-- the cap on the interval that rule produces, AIM UP how far above the
-- vehicle's own origin the spot is held, INFRARED whether an IR pointer goes up
-- beside the laser, TRACE whether every decision is written to `dcs.log`.
--
-- Why this exists at all is in `core/laser.py`'s `arm_autolase`: DCS's own AI
-- controller lases as part of a radio conversation the player has to be in
-- range and in line of sight to have, and a flight coming out of a valley onto
-- a moving target has neither the seconds nor the sight line to have it.
--
-- **The method here is Ciribob's, from DCS-CTLD's JTAC autolase**
-- (https://github.com/ciribob/DCS-CTLD, `ctld.JTACAutoLase` /
-- `ctld.laseUnit` / `ctld.findNearestVisibleEnemy`), reimplemented rather than
-- vendored — CTLD carries no licence and requires MIST, which this project
-- deliberately does not ship (see `core/lua/vendor/README.md`). What is taken
-- from it, and why each one matters, is marked CTLD below. The rest of what
-- CTLD's JTAC does — the F10 status menu, the 9-line text, smoke marking,
-- priority-by-unit-name — is deliberately absent: this project has its own
-- readout (`core/jtac.py`), its own radio calls (`core/triggers.py`) and one
-- target per designation, named by the mission rather than searched for.
--
-- What is modelled is a team already on the target: the spot is up whenever the
-- designator can see a vehicle inside its own reach, and off whenever it
-- cannot. Nothing about the player is read — no check-in, no distance, no slot
-- — because a spot that waits for the jet is the same defect one step further
-- down the road.
do
  local spots = {
__SPOTS__
  }
  local verifyEvery = __VERIFY__
  local maxDrift = __DRIFT__
  local minTick = __TICK__
  local maxTick = __CEILING__
  local aimUp = __AIM_UP__
  local infrared = __INFRARED__
  local trace = __TRACE__

  -- CTLD's own correction factors, in seconds of travel: the spot is pushed one
  -- second ahead of the vehicle and 1.05 s upwind, which is what makes an LGB
  -- arrive on a moving truck rather than in its dust. Off unless a mission asks
  -- for it — it is a lead the crew is not really computing.
  local LEAD_S, WIND_S = 1.0, 1.05

  local function log(spot, fmt, ...)
    if trace then
      env.info("LASER/" .. spot.label .. ": " .. string.format(fmt, ...))
    end
  end

  local function firstLive(name)
    local group = Group.getByName(name)
    if group == nil or not group:isExist() then return nil end
    local units = group:getUnits()
    if units == nil then return nil end
    for _, unit in ipairs(units) do
      if unit:isExist() and unit:getLife() > 0 then return unit end
    end
    return nil
  end

  -- CTLD: both ends offset 2 m off the deck. `getPoint` is a unit's origin,
  -- which sits on the ground, and two ground points fail `isVisible` across the
  -- gentlest rise even where the crews can see each other perfectly well —
  -- "rounding errors can cause issues, plus the unit has some height anyways".
  local function eyeLine(from, to)
    return land.isVisible({x = from.x, y = from.y + 2.0, z = from.z},
                          {x = to.x, y = to.y + 2.0, z = to.z})
  end

  local function flatRange(a, b)
    local dx, dz = a.x - b.x, a.z - b.z
    return math.sqrt(dx * dx + dz * dz)
  end

  local function velocity(unit)
    local ok, v = pcall(function() return unit:getVelocity() end)
    if not ok or v == nil then return {x = 0, y = 0, z = 0} end
    return v
  end

  local function speed(unit)
    local v = velocity(unit)
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
  end

  local function usable(spot, observer, unit)
    if unit == nil or not unit:isExist() or unit:getLife() <= 0 then return false end
    -- CTLD excludes anything airborne: a laser spot is for something on the
    -- ground, and a helicopter overhead would take the designation off the road.
    local ok, air = pcall(function() return unit:inAir() end)
    if ok and air then return false end
    local point = unit:getPoint()
    if flatRange(observer:getPoint(), point) > spot.range then return false end
    return not spot.los or eyeLine(observer:getPoint(), point)
  end

  -- CTLD: the aim point is the vehicle's own origin lifted clear of the deck,
  -- optionally led into its movement and away from the wind.
  local function aimPoint(spot, unit)
    local p = unit:getPoint()
    local point = {x = p.x, y = p.y + aimUp, z = p.z}
    if spot.lead then
      local v = velocity(unit)
      point.x, point.y, point.z =
        point.x + v.x * LEAD_S, point.y + v.y * LEAD_S, point.z + v.z * LEAD_S
      local ok, wind = pcall(function() return atmosphere.getWind(point) end)
      if ok and wind ~= nil then
        point.x, point.y, point.z =
          point.x - wind.x * WIND_S, point.y - wind.y * WIND_S,
          point.z - wind.z * WIND_S
      end
    end
    return point
  end

  local function drop(spot, why)
    if spot.laser ~= nil or spot.ir ~= nil then
      pcall(function() if spot.laser then spot.laser:destroy() end end)
      pcall(function() if spot.ir then spot.ir:destroy() end end)
      spot.laser, spot.ir, spot.source, spot.aim = nil, nil, nil, nil
      log(spot, "spot off — %s", why)
    end
  end

  -- CTLD `findNearestVisibleEnemy`, narrowed to the one group the mission named:
  -- the nearest vehicle the designator can both see and reach. The one already
  -- being lased wins while it still qualifies (CTLD's `getCurrentUnit`), because
  -- a spot that hops to a closer truck while a bomb is in the air throws the
  -- weapon off the vehicle the pilot was talked onto.
  local function pick(spot, observer)
    if spot.aim ~= nil then
      local held = Unit.getByName(spot.aim)
      if usable(spot, observer, held) then return held end
    end
    local group = Group.getByName(spot.target)
    if group == nil or not group:isExist() then return nil end
    local units = group:getUnits()
    if units == nil then return nil end
    local best, bestRange
    for _, unit in ipairs(units) do
      if usable(spot, observer, unit) then
        local range = flatRange(observer:getPoint(), unit:getPoint())
        if bestRange == nil or range < bestRange then best, bestRange = unit, range end
      end
    end
    return best
  end

  local function create(spot, observer, point)
    -- CTLD: the beam leaves the designating vehicle 2 m up, and the IR pointer
    -- goes on the same point so a TGP or a set of goggles finds the target
    -- without the laser having to be the only mark.
    local ok, made = pcall(function()
      local out = {}
      if infrared then
        out.ir = Spot.createInfraRed(observer, {x = 0, y = 2.0, z = 0}, point)
      end
      out.laser = Spot.createLaser(observer, {x = 0, y = 2.0, z = 0}, point, spot.code)
      return out
    end)
    if not ok or made == nil or made.laser == nil then
      log(spot, "Spot.createLaser failed — %s", tostring(made))
      return false
    end
    spot.laser, spot.ir, spot.source = made.laser, made.ir, observer:getName()
    return true
  end

  -- How long the spot may be left where it is: CTLD's rule, that the vehicle
  -- never travels more than `maxDrift` metres between two updates. A stationary
  -- target needs nothing until the next verification pass.
  local function interval(unit)
    local v = speed(unit)
    if v <= 0.0 then return maxTick end
    return math.max(minTick, math.min(maxTick, maxDrift / v))
  end

  local function run(spot, now)
    if now < spot.startAt then return spot.startAt end
    local observer = firstLive(spot.observer)
    if observer == nil then
      -- Never seen alive yet means not activated yet, which is a group the
      -- mission holds back rather than one the player lost: keep looking.
      -- Seen and now gone is the team being dead, and then the laser is over.
      if not spot.seen then return now + verifyEvery end
      drop(spot, "the designating team is gone")
      return nil
    end
    spot.seen = true
    -- The heavy pass — range, line of sight, which vehicle — on its own slower
    -- clock; in between, the spot is only moved. Verifying every tick would put
    -- a terrain query per vehicle per fifth of a second on the server.
    local aim = spot.aim ~= nil and Unit.getByName(spot.aim) or nil
    if aim == nil or now >= (spot.verifyAt or 0) then
      aim = pick(spot, observer)
      spot.verifyAt = now + verifyEvery
    end
    if aim == nil then
      drop(spot, "nothing in reach with a sight line")
      return now + verifyEvery
    end
    local point = aimPoint(spot, aim)
    -- A spot belongs to the vehicle it was created from, so it dies with it and
    -- is rebuilt on whoever of the team is left.
    if spot.laser ~= nil and spot.source ~= observer:getName() then
      drop(spot, "the designator changed vehicle")
    end
    if spot.laser == nil then
      if not create(spot, observer, point) then return now + verifyEvery end
      spot.aim = aim:getName()
      log(spot, "lasing %s on %d from %s", spot.aim, spot.code, spot.source)
      return now + interval(aim)
    end
    if spot.aim ~= aim:getName() then
      spot.aim = aim:getName()
      log(spot, "shifted the spot to %s", spot.aim)
    end
    pcall(function() spot.laser:setPoint(point) end)
    pcall(function() if spot.ir then spot.ir:setPoint(point) end end)
    return now + interval(aim)
  end

  for _, spot in ipairs(spots) do
    timer.scheduleFunction(run, spot, timer.getTime() + 1)
  end
end
