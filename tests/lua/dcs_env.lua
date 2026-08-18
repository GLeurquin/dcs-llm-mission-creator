-- A stand-in for the DCS mission-scripting environment, used by
-- `tests/test_iads_runtime.py` to actually *run* the MIST shim, the vendored
-- Skynet-IADS build and a generated setup script under an embedded Lua.
--
-- This exists because "the Lua compiles" is a much weaker claim than it sounds:
-- the interesting failures in an IADS integration are all runtime ones — a
-- method that moved, a site that never gets cued, suppression that does not
-- stick. Every stub below is shaped the way DCS actually behaves, because
-- Skynet leans on the details; the comments mark the ones that are load-bearing.
--
-- Test-facing entry points:
--   TESTGROUP{name=, x=, z=, side=, category=, unitCategory=, units={...}}
--       Places a group. Each unit takes {type=, dx=, dz=, y=, attrs=, ammo=,
--       radar_m=, missile_m=, ceiling_m=}. `radar_m` is head-on air detection
--       range, `missile_m` the launcher's reach, `ceiling_m` its altitude limit.
--   TESTADVANCE(to, step)   run the clock forward, firing scheduled tasks
--   TESTFIRE(event)         deliver a world event to every handler
--   TESTMASK = f(a, b)      override terrain line of sight (nil = everything visible)
--   TESTLOG                 list of noted lines; assign a fresh table to clear
do
  -- Appends to the *current* _G.TESTLOG, so a test can clear it between phases
  -- by assigning a fresh table. Capturing the table in a local instead silently
  -- decouples the writer from the reader.
  _G.TESTLOG = {}
  local function note(s)
    local t = _G.TESTLOG
    t[#t + 1] = s
  end
  _G.TESTNOTE = note

  env = {
    info = function(s) note("INFO " .. tostring(s)) end,
    warning = function(s) note("WARN " .. tostring(s)) end,
    error = function(s) note("ERROR " .. tostring(s)) end,
  }

  -- ------------------------------------------------------------------- clock
  local now = 0
  local tasks = {}
  timer = {
    getTime = function() return now end,
    getAbsTime = function() return 28800 + now end,
    scheduleFunction = function(f, arg, t)
      tasks[#tasks + 1] = {f = f, arg = arg, t = t}
      return #tasks
    end,
    removeFunction = function() end,
  }
  function TESTADVANCE(to, step)
    step = step or 1
    while now < to do
      now = now + step
      local due, keep = {}, {}
      for _, task in ipairs(tasks) do
        if task.t <= now then due[#due + 1] = task else keep[#keep + 1] = task end
      end
      tasks = keep
      for _, task in ipairs(due) do
        FIRED = (FIRED or 0) + 1
        local ok, again = pcall(task.f, task.arg, now)
        if not ok then note("SCHEDERR " .. tostring(again))
        elseif type(again) == "number" then
          tasks[#tasks + 1] = {f = task.f, arg = task.arg, t = again}
        end
      end
    end
  end

  -- ------------------------------------------------------------- enumerations
  coalition = {side = {NEUTRAL = 0, RED = 1, BLUE = 2}}
  Unit = {Category = {AIRPLANE = 0, HELICOPTER = 1, GROUND_UNIT = 2, SHIP = 3},
          SensorType = {OPTIC = 0, RADAR = 1, IRST = 2, RWR = 3}}
  Group = {Category = {AIRPLANE = 0, HELICOPTER = 1, GROUND = 2, SHIP = 3}}
  Object = {Category = {UNIT = 1, WEAPON = 2, STATIC = 3}}
  Weapon = {GuidanceType = {RADAR_PASSIVE = 3},
            Category = {SHELL = 0, MISSILE = 1, ROCKET = 2, BOMB = 3}}
  Controller = {Detection = {VISUAL = 1, OPTIC = 2, RADAR = 4, IRST = 8}}
  AI = {
    Option = {
      Ground = {id = {ROE = 0, ALARM_STATE = 9},
                val = {ROE = {OPEN_FIRE = 2, WEAPON_HOLD = 4},
                       ALARM_STATE = {AUTO = 0, GREEN = 1, RED = 2}}},
      Air = {id = {ROE = 0}, val = {ROE = {WEAPON_FREE = 0}}},
    },
  }
  trigger = {action = {
    outTextForCoalition = function(_, text) note("TEXT " .. tostring(text)) end,
    outSoundForCoalition = function(_, snd) note("SOUND " .. tostring(snd)) end,
    outText = function(text) note("DEBUG " .. tostring(text)) end,
    outSound = function(snd) note("SOUND " .. tostring(snd)) end,
  }}
  missionCommands = {
    addSubMenuForCoalition = function() return {} end,
    addCommandForCoalition = function() return {} end,
    removeItemForCoalition = function() end,
  }
  coord = {
    LOtoLL = function(p) return p.x / 111000, p.z / 111000 end,
    LLtoLO = function(lat, lon) return {x = lat * 111000, y = 0, z = lon * 111000} end,
  }

  -- Skynet registers the IADS *and* every element as an event handler, and it is
  -- S_EVENT_DEAD that tells a battery its parent radar is gone. Removal has to
  -- work too: cleanUp() calls it, and a stale handler on a destroyed element
  -- keeps answering.
  local handlers = {}
  world = {
    event = {S_EVENT_SHOT = 1, S_EVENT_DEAD = 8, S_EVENT_BIRTH = 15},
    addEventHandler = function(h) handlers[#handlers + 1] = h end,
    removeEventHandler = function(h)
      for i, existing in ipairs(handlers) do
        if existing == h then table.remove(handlers, i) return end
      end
    end,
  }
  function TESTFIRE(event)
    -- Iterate a copy: a handler may remove itself while the event is delivered.
    local snapshot = {}
    for i, h in ipairs(handlers) do snapshot[i] = h end
    for _, h in ipairs(snapshot) do
      local ok, err = pcall(h.onEvent, h, event)
      if not ok then note("SCHEDERR handler: " .. tostring(err)) end
    end
  end

  -- --------------------------------------------------------------- the world
  -- x is north, z is east, y is altitude.
  local units, groups = {}, {}

  -- In DCS the class tables *are* the metatables: getmetatable(group) == Group.
  -- Skynet branches on exactly that, so the stub has to match.
  local UnitMT = Unit
  UnitMT.__index = UnitMT
  function UnitMT:getName() return self.name end
  function UnitMT:getTypeName() return self.type end
  function UnitMT:isExist() return self.alive end
  function UnitMT:isActive() return self.alive end
  function UnitMT:getLife() return self.alive and 100 or 0 end
  function UnitMT:getPoint() return {x = self.x, y = self.y or 0, z = self.z} end
  function UnitMT:getPosition()
    return {p = self:getPoint(), x = {x = 1, y = 0, z = 0}}
  end
  function UnitMT:getDesc() return {category = self.category or Unit.Category.GROUND_UNIT} end
  function UnitMT:inAir() return (self.y or 0) > 10 end
  function UnitMT:hasAttribute(a) return self.attrs and self.attrs[a] == true end
  function UnitMT:getGroup() return self.group end
  -- Object.Category, not Unit.Category: Skynet asks contacts what kind of object
  -- they are before handing them to a SAM site.
  function UnitMT:getCategory() return Object.Category.UNIT end
  function UnitMT:getCoalition() return self.group.side end
  -- An EWR's DCS representation is its Unit, so it needs a controller of its own.
  function UnitMT:getController() return self.group.controller end
  function UnitMT:getID() return self.name end
  function UnitMT:enableEmission(on) self.emitting = on end
  -- Launcher ammunition, the shape Skynet reads it in. `rangeMaxAlt*` is what
  -- it uses as the site's kill-zone radius, which is what go_live_percent scales.
  function UnitMT:getAmmo()
    if self.ammo == nil or self.ammo <= 0 then return nil end
    return {{count = self.ammo, desc = {
      category = Weapon.Category.MISSILE, missileCategory = 2,
      typeName = self.type .. " missile", displayName = "missile",
      rangeMaxAltMin = self.missile_m or 20000,
      rangeMaxAltMax = self.missile_m or 20000,
      altMax = self.ceiling_m or 10000}}}
  end
  -- Radar detection range, the shape Skynet reads it in: getSensors() returns
  -- a list of lists. `radar_m` on the spec is the head-on air detection range.
  function UnitMT:getSensors()
    if self.radar_m == nil then return nil end
    return {{{type = Unit.SensorType.RADAR,
              detectionDistanceAir = {
                upperHemisphere = {headOn = self.radar_m, tailOn = self.radar_m},
                lowerHemisphere = {headOn = self.radar_m, tailOn = self.radar_m}}}}}
  end
  function UnitMT:destroy() self.alive = false end

  local GroupMT = Group
  GroupMT.__index = GroupMT
  function GroupMT:getName() return self.name end
  function GroupMT:isExist()
    for _, u in ipairs(self.units) do if u.alive then return true end end
    return false
  end
  function GroupMT:getUnits()
    local out = {}
    for _, u in ipairs(self.units) do if u.alive then out[#out + 1] = u end end
    return out
  end
  function GroupMT:getSize() return #self:getUnits() end
  function GroupMT:getCategory() return self.category end
  function GroupMT:getCoalition() return self.side end
  function GroupMT:getController() return self.controller end
  -- A SAM site's DCS representation is its Group, so the emission switch and the
  -- id accessor have to exist on both.
  function GroupMT:enableEmission(on)
    self.emitting = on
    for _, u in ipairs(self.units) do u.emitting = on end
  end
  function GroupMT:getID() return self.name end

  local function makeController(owner)
    return {
      setOption = function(_, id, val)
        if id == AI.Option.Ground.id.ALARM_STATE then owner.alarm = val end
        if id == AI.Option.Ground.id.ROE then owner.roe = val end
      end,
      setOnOff = function(_, on) owner.onoff = on end,
      -- What this group's radars hold. Modelled the way DCS behaves, because
      -- Skynet leans on it hard: a *live* site keeps itself live by still
      -- detecting its target (see goDark), and a site with emissions off
      -- detects nothing at all, which is what makes suppression stick.
      getDetectedTargets = function()
        local out = {}
        if owner.emitting == false then return out end
        for _, u in ipairs(owner.units) do
          if u.alive and u.radar_m and u.attrs then
            for _, side in ipairs({coalition.side.BLUE, coalition.side.RED}) do
              if side ~= owner.side then
                for _, g in pairs(coalition.getGroups(side)) do
                  for _, t in ipairs(g:getUnits()) do
                    if t:inAir() then
                      local a, b = u:getPoint(), t:getPoint()
                      local dx, dz = a.x - b.x, a.z - b.z
                      local d = math.sqrt(dx * dx + dz * dz)
                      if d <= u.radar_m and land.isVisible(a, b) then
                        local dup = false
                        for _, e in ipairs(out) do
                          if e.object == t then dup = true break end
                        end
                        if not dup then
                          out[#out + 1] = {object = t, visible = true, distance = d}
                        end
                      end
                    end
                  end
                end
              end
            end
          end
        end
        return out
      end,
    }
  end

  function TESTGROUP(spec)
    local g = setmetatable({name = spec.name, side = spec.side or coalition.side.RED,
                            category = spec.category or Group.Category.GROUND,
                            units = {}}, GroupMT)
    g.controller = makeController(g)
    for i, u in ipairs(spec.units) do
      local unit = setmetatable({
        name = spec.name .. " Unit #" .. i, type = u.type, alive = true,
        x = spec.x + (u.dx or 0), z = spec.z + (u.dz or 0), y = u.y or 0,
        attrs = u.attrs, ammo = u.ammo, radar_m = u.radar_m,
        missile_m = u.missile_m, ceiling_m = u.ceiling_m, group = g,
        category = spec.unitCategory or Unit.Category.GROUND_UNIT,
      }, UnitMT)
      units[unit.name] = unit
      g.units[i] = unit
    end
    groups[g.name] = g
    return g
  end

  Group.getByName = function(n) return groups[n] end
  Unit.getByName = function(n) return units[n] end
  StaticObject = {getByName = function() return nil end}

  coalition.getGroups = function(side, cat)
    local out = {}
    for _, g in pairs(groups) do
      if g.side == side and (cat == nil or g.category == cat) and g:isExist() then
        out[#out + 1] = g
      end
    end
    return out
  end

  -- Flat terrain: everything sees everything. Overridden per test to mask.
  land = {
    isVisible = function(a, b)
      if TESTMASK then return TESTMASK(a, b) end
      return true
    end,
    getHeight = function() return 0 end,
  }
end
