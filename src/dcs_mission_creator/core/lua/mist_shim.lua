-- dcs_mission_creator: the slice of MIST that Skynet-IADS actually calls.
--
-- Skynet documents MIST as a prerequisite, but MIST is GPL-3.0 and 313 KB, and
-- Skynet calls exactly thirteen of its functions. Shipping a copyleft library
-- inside every generated .miz to get a scheduler and seven unit conversions is
-- a poor trade, so this is a first-party implementation of that surface,
-- written from each function's documented behaviour. See
-- `core/lua/vendor/README.md`.
--
-- Loaded before `vendor/skynet-iads.lua`. It defines only what Skynet reads:
--   mist.scheduleFunction / mist.removeFunction
--   mist.random, mist.getHeading
--   mist.utils.round / metersToNM / metersToFeet / toDegree
--   mist.utils.get2DDist / get3DDist / getHeadingPoints
--   mist.DBs.unitsByName / mist.DBs.groupsByName
-- Anything else in MIST is absent on purpose: if a future Skynet build reaches
-- for it, the mission fails loudly at start-up rather than misbehaving in the
-- air, which is the failure mode you want from a shim.
do
  mist = mist or {}
  mist.utils = mist.utils or {}
  mist.vec = mist.vec or {}

  -- DCS runs Lua 5.1, where this exists; the alias keeps the file compilable
  -- under a modern standalone Lua so it can be checked outside the game.
  local atan2 = math.atan2 or function(y, x) return math.atan(y, x) end
  local unpack = unpack or table.unpack

  -- ------------------------------------------------------------ conversions --

  function mist.utils.round(num, idp)
    local mult = 10 ^ (idp or 0)
    return math.floor(num * mult + 0.5) / mult
  end

  function mist.utils.metersToNM(meters)
    return meters / 1852
  end

  function mist.utils.metersToFeet(meters)
    return meters / 0.3048
  end

  function mist.utils.toDegree(angle)
    return angle * 180 / math.pi
  end

  -- ---------------------------------------------------------------- vectors --

  function mist.vec.mag(vec)
    return (vec.x ^ 2 + vec.y ^ 2 + vec.z ^ 2) ^ 0.5
  end

  -- A DCS Vec2 uses `y` for what a Vec3 calls `z`, so anything taking "a point"
  -- has to accept both. MIST normalises with makeVec3; this does the same, and
  -- returns a copy so a caller's table is never mutated.
  local function vec3(p)
    if p.z == nil then return {x = p.x, y = 0, z = p.y} end
    return {x = p.x, y = p.y or 0, z = p.z}
  end

  function mist.utils.get2DDist(point1, point2)
    local a, b = vec3(point1), vec3(point2)
    return mist.vec.mag({x = a.x - b.x, y = 0, z = a.z - b.z})
  end

  function mist.utils.get3DDist(point1, point2)
    local a, b = vec3(point1), vec3(point2)
    return mist.vec.mag({x = a.x - b.x, y = a.y - b.y, z = a.z - b.z})
  end

  -- Grid convergence: DCS's x/z grid is not aligned to true north except on the
  -- map's reference meridian. Step one degree of latitude north of the point,
  -- convert back, and the offset of that step from grid-north is the correction.
  function mist.getNorthCorrection(gPoint)
    local point = vec3(gPoint)
    local lat, lon = coord.LOtoLL(point)
    local north = coord.LLtoLO(lat + 1, lon)
    return atan2(north.z - point.z, north.x - point.x)
  end

  function mist.utils.getDir(vec, point)
    local dir = atan2(vec.z, vec.x)
    if point then dir = dir + mist.getNorthCorrection(point) end
    if dir < 0 then dir = dir + 2 * math.pi end
    return dir
  end

  function mist.utils.getHeadingPoints(point1, point2, north)
    local a, b = vec3(point1), vec3(point2)
    local delta = {x = b.x - a.x, y = b.y - a.y, z = b.z - a.z}
    if north then return mist.utils.getDir(delta, a) end
    return mist.utils.getDir(delta)
  end

  -- A unit's own heading, from the forward axis of its orientation matrix.
  -- `rawHeading` skips the true-north correction.
  function mist.getHeading(unit, rawHeading)
    local pos = unit:getPosition()
    if pos == nil then return nil end
    local heading = atan2(pos.x.z, pos.x.x)
    if not rawHeading then heading = heading + mist.getNorthCorrection(pos.p) end
    if heading < 0 then heading = heading + 2 * math.pi end
    return heading
  end

  -- ----------------------------------------------------------------- random --

  -- MIST draws several times over a widened range to flatten `math.random`'s
  -- behaviour on small integer spans. The distribution is uniform either way,
  -- and Skynet uses this for one HARM timing band, so the plain draw is honest.
  -- Integers only, as MIST documents.
  function mist.random(firstNum, secondNum)
    local low, high
    if secondNum == nil then
      low, high = 1, firstNum
    else
      low, high = firstNum, secondNum
    end
    low, high = math.floor(low + 0.5), math.floor(high + 0.5)
    if low > high then low, high = high, low end
    return math.random(low, high)
  end

  -- -------------------------------------------------------------- scheduler --
  --
  -- MIST's scheduler differs from `timer.scheduleFunction` in three ways Skynet
  -- relies on: `vars` is an argument *list* that gets unpacked, `rep` makes the
  -- task repeat on that interval forever, and the returned id can be cancelled
  -- later by `removeFunction`. Built on top of the DCS timer, with cancellation
  -- checked at fire time so an id removed mid-interval simply never runs again.
  --
  -- One deliberate improvement over MIST: the call is wrapped in `pcall`. A
  -- repeating Skynet task that throws would otherwise take the whole IADS down
  -- silently at some point mid-mission; this logs it and keeps the cycle alive.

  local nextTaskId = 0
  local cancelled = {}

  function mist.scheduleFunction(f, vars, t, rep, st)
    assert(type(f) == "function", "mist.scheduleFunction: 1st argument must be a function")
    assert(type(t) == "number", "mist.scheduleFunction: 3rd argument must be a number")
    vars = vars or {}
    nextTaskId = nextTaskId + 1
    local id = nextTaskId

    local function fire(_, now)
      if cancelled[id] then
        cancelled[id] = nil
        return nil
      end
      local ok, err = pcall(f, unpack(vars))
      if not ok then
        env.error("mist_shim: scheduled function " .. tostring(id) .. " failed: " .. tostring(err))
      end
      if cancelled[id] then
        cancelled[id] = nil
        return nil
      end
      -- `st` is MIST's stop time: repeat until then, if given.
      if rep and (st == nil or now + rep <= st) then return now + rep end
      return nil
    end

    timer.scheduleFunction(fire, {}, t)
    return id
  end

  function mist.removeFunction(id)
    if id == nil then return false end
    cancelled[id] = true
    return true
  end

  -- ---------------------------------------------------------------- name DBs --
  --
  -- MIST keeps tables of every unit and group in the mission, rebuilt on spawn
  -- events. Skynet reads them in `addSAMSitesByPrefix` /
  -- `addEarlyWarningRadarsByPrefix` only, and `core/iads.py` registers every
  -- site by its exact name instead, so the prefix paths are never taken. These
  -- are populated at load time anyway — cheaply, from the coalition lists — so
  -- that a prefix call returns a sane answer for anything alive at mission
  -- start rather than silently finding nothing.

  mist.DBs = mist.DBs or {}
  mist.DBs.unitsByName = {}
  mist.DBs.groupsByName = {}

  local function indexNames()
    for _, side in pairs({coalition.side.NEUTRAL, coalition.side.RED, coalition.side.BLUE}) do
      for _, cat in pairs(Group.Category) do
        for _, group in ipairs(coalition.getGroups(side, cat) or {}) do
          if group:isExist() then
            mist.DBs.groupsByName[group:getName()] = {groupName = group:getName()}
            for _, unit in ipairs(group:getUnits() or {}) do
              mist.DBs.unitsByName[unit:getName()] = {unitName = unit:getName()}
            end
          end
        end
      end
    end
  end

  local ok, err = pcall(indexNames)
  if not ok then
    env.error("mist_shim: could not index unit and group names: " .. tostring(err))
  end
end
