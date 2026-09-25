-- dcs_mission_creator: intercept, shadow and escort an intruder out of a zone.
--
-- Rendered by `core/intercept.py` into a mission-start DoScript. Placeholders
-- (named here without their underscores so this comment is not substituted):
-- TRACKS is one row per intruder group, ZONE the boundary polygon as `{x=, y=}`
-- rows, SIDE the players' coalition, INTERCEPT M / HOLD S how close a player
-- has to be and for how long, ESCORT M / LAPSE S how far a player may drift and
-- for how long before an escorted track turns back in, TICK the poll period,
-- CONTROLLER the group whose radio every call goes out on (nil for none),
-- DEFENDERS the friendly AI groups committed on a hostile, the three FLAG
-- numbers, and TRACE.
--
-- The decisions are the Python module's docstring; what is here is the state
-- machine, one per track:
--
--   pending   late-activated, not yet flying
--   loose     inside the zone on its own route, nobody alongside
--   escorted  a player held a position on it, it is flying home
--   hostile   it turned on the escort; the defenders are committed
--   resolved  out of the zone, or dead
do
  local tracks = {
__TRACKS__
  }
  local zone = {
__ZONE__
  }
  local side = __SIDE__
  local interceptM = __INTERCEPT_M__
  local holdS = __HOLD_S__
  local escortM = __ESCORT_M__
  local lapseS = __LAPSE_S__
  local tick = __TICK__
  local controller = __CONTROLLER__
  local defenders = {__DEFENDERS__}
  local flagDone, flagFoul, flagUnchallenged = __FLAG_DONE__, __FLAG_FOUL__, __FLAG_UNCHALLENGED__
  local trace = __TRACE__

  local byGroup = {}
  for _, t in ipairs(tracks) do
    t.state = "pending"
    t.near = 0
    t.away = 0
    t.epoch = 0
    byGroup[t.group] = t
  end

  local function log(t, fmt, ...)
    if trace then
      env.info(string.format("INTERCEPT/%s t+%.0fs: ", t.label, timer.getTime())
        .. string.format(fmt, ...))
    end
  end

  -- Calls go out one at a time, and only while the controller is alive to make
  -- them: a picture nobody is left to transmit is not transmitted.
  local queue, draining = {}, false
  local function controllerAlive()
    if controller == nil then return true end
    local g = Group.getByName(controller)
    if g == nil or not g:isExist() then return false end
    for _, u in ipairs(g:getUnits()) do
      if u:isExist() and u:getLife() > 0 then return true end
    end
    return false
  end
  local function playNext(_, time)
    local item = table.remove(queue, 1)
    if item == nil then
      draining = false
      return nil
    end
    if item.text then trigger.action.outTextForCoalition(side, item.text, 15) end
    if item.sound then trigger.action.outSoundForCoalition(side, item.sound) end
    return time + 8
  end
  local function say(t, key)
    local call = t.calls[key]
    if call == nil or not controllerAlive() then return end
    queue[#queue + 1] = call
    if not draining then
      draining = true
      timer.scheduleFunction(playNext, {}, timer.getTime() + 0.1)
    end
  end

  -- Ray casting against the boundary, in DCS x (north) / z (east).
  local function inside(p)
    local hit = false
    local j = #zone
    for i = 1, #zone do
      local a, b = zone[i], zone[j]
      if ((a.y > p.z) ~= (b.y > p.z))
        and (p.x < (b.x - a.x) * (p.z - a.y) / (b.y - a.y) + a.x) then
        hit = not hit
      end
      j = i
    end
    return hit
  end

  local function live(t)
    local g = Group.getByName(t.group)
    if g == nil or not g:isExist() then return {} end
    local out = {}
    for _, u in ipairs(g:getUnits()) do
      if u:isExist() and u:isActive() and u:getLife() > 0 then out[#out + 1] = u end
    end
    return out
  end

  local function nearestPlayer(units)
    local best, bestD = nil, math.huge
    for _, pu in ipairs(coalition.getPlayers(side)) do
      if pu:isExist() and pu:inAir() then
        local pp = pu:getPoint()
        for _, u in ipairs(units) do
          local up = u:getPoint()
          local dx, dy, dz = pp.x - up.x, pp.y - up.y, pp.z - up.z
          local d = math.sqrt(dx * dx + dy * dy + dz * dz)
          if d < bestD then best, bestD = pu, d end
        end
      end
    end
    return best, bestD
  end

  local function lead(units)
    return units[1]:getPoint()
  end

  local function turningPoint(p, alt, speed, tasks)
    return {
      type = "Turning Point", action = "Turning Point",
      x = p.x, y = p.y, alt = alt, alt_type = "BARO", speed = speed,
      task = {id = "ComboTask", params = {tasks = tasks or {}}},
    }
  end

  local function homeRoute(t, from, extra)
    return {
      turningPoint({x = from.x, y = from.z}, t.alt, t.speed, extra),
      turningPoint(t.exit, t.alt, t.speed),
      {
        type = "Land", action = "Landing", airdromeId = t.airdrome,
        x = t.home.x, y = t.home.y, alt = 0, alt_type = "BARO", speed = t.speed,
      },
    }
  end

  local function setMission(t, points)
    local g = Group.getByName(t.group)
    if g == nil then return end
    g:getController():setTask({id = "Mission", params = {route = {points = points}}})
  end

  local function setRoe(groupName, value)
    local g = Group.getByName(groupName)
    if g == nil or not g:isExist() then return end
    g:getController():setOption(AI.Option.Air.id.ROE, value)
  end

  -- The only entropy a mission script has is the player: a millisecond clock
  -- read at the moment somebody arrives alongside is different every sortie,
  -- where the stock generator replays the same stream from mission start.
  local function reseed(p)
    math.randomseed(math.floor(timer.getTime() * 1000) + math.floor(math.abs(p.x) % 997))
    math.random(); math.random()
  end

  local function sendHome(t, units)
    setRoe(t.group, AI.Option.Air.val.ROE.WEAPON_HOLD)
    setMission(t, homeRoute(t, lead(units)))
  end

  local function turnHostile(t, units)
    t.state = "hostile"
    local escort = nearestPlayer(units)
    local via = escort and escort:getPoint() or lead(units)
    setRoe(t.group, AI.Option.Air.val.ROE.WEAPON_FREE)
    local g = Group.getByName(t.group)
    g:getController():setOption(AI.Option.Air.id.REACTION_ON_THREAT,
      AI.Option.Air.val.REACTION_ON_THREAT.EVADE_FIRE)
    local engage = {id = "EngageTargets", params = {
      targetTypes = {"Air"}, priority = 0, maxDist = 80000,
    }}
    local points = homeRoute(t, lead(units), {engage})
    table.insert(points, 2, turningPoint({x = via.x, y = via.z}, t.alt, t.speed * 1.2))
    setMission(t, points)
    -- The defenders are held on OPEN_FIRE, which in DCS is "only what you are
    -- tasked against", so committing them on this group cannot turn them on a
    -- compliant track two miles away.
    for _, name in ipairs(defenders) do
      local d = Group.getByName(name)
      if d and d:isExist() then
        d:getController():setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.OPEN_FIRE)
        d:getController():pushTask({id = "AttackGroup", params = {groupId = g:getID()}})
      end
    end
    log(t, "hostile; %d defender group(s) committed", #defenders)
    say(t, "hostile")
  end

  local function scheduleRoll(t, units)
    reseed(lead(units))
    local roll = math.random()
    if roll >= t.p then
      log(t, "roll %.2f >= %.2f, stays compliant", roll, t.p)
      return
    end
    local wait = t.after[1] + (math.random() + math.random()) * 0.5 * (t.after[2] - t.after[1])
    local epoch = t.epoch
    log(t, "roll %.2f < %.2f, turns in %.0fs if still escorted", roll, t.p, wait)
    timer.scheduleFunction(function()
      if t.state ~= "escorted" or t.epoch ~= epoch then return nil end
      local now = live(t)
      if #now == 0 or not inside(now[1]:getPoint()) then return nil end
      turnHostile(t, now)
      return nil
    end, {}, timer.getTime() + wait)
  end

  local function resolve(t, how)
    t.state = "resolved"
    t.how = how
    log(t, "resolved: %s", how)
    for _, other in ipairs(tracks) do
      if other.state ~= "resolved" then return end
    end
    trigger.action.setUserFlag(flagDone, 1)
  end

  local function step(t, now)
    if t.state == "resolved" then return end
    local units = live(t)
    if t.state == "pending" then
      if #units == 0 then return end
      t.state = "loose"
      t.since = now
      log(t, "active")
      return
    end
    if #units == 0 then
      say(t, t.state == "hostile" and "splashed" or "down")
      resolve(t, t.state == "hostile" and "splashed" or "down")
      return
    end
    local p = lead(units)
    local _, d = nearestPlayer(units)

    if t.state == "loose" then
      if t.unchallenged then
        if not inside(p) then
          resolve(t, "left unchallenged")
        end
        return
      end
      t.near = (d <= interceptM) and (t.near + tick) or 0
      if t.near >= holdS then
        t.state = "escorted"
        t.away, t.epoch = 0, t.epoch + 1
        log(t, "intercepted at %.0f m", d)
        sendHome(t, units)
        say(t, "intercepted")
        scheduleRoll(t, units)
      elseif now - t.since >= t.deadline then
        t.unchallenged = true
        trigger.action.setUserFlag(flagUnchallenged, 1)
        log(t, "deadline passed with nobody alongside")
        setMission(t, homeRoute(t, p))
        say(t, "unchallenged")
      end
      return
    end

    if not inside(p) then
      say(t, t.state == "hostile" and "fled" or "cleared")
      resolve(t, t.state == "hostile" and "fled" or "escorted out")
      return
    end

    if t.state == "escorted" then
      t.away = (d > escortM) and (t.away + tick) or 0
      if t.away >= lapseS then
        t.state, t.near, t.epoch = "loose", 0, t.epoch + 1
        log(t, "escort lapsed at %.0f m, back to the orbit", d)
        setMission(t, {
          turningPoint({x = p.x, y = p.z}, t.alt, t.speed),
          turningPoint(t.orbit, t.alt, t.speed, {{id = "Orbit", params = {
            pattern = "Circle", altitude = t.alt, speed = t.speed,
          }}}),
        })
        say(t, "lapsed")
      end
    end
  end

  local function poll(_, time)
    for _, t in ipairs(tracks) do
      local ok, err = pcall(step, t, time)
      if not ok then env.error("INTERCEPT/" .. t.label .. ": " .. tostring(err)) end
    end
    return time + tick
  end

  -- A weapon on a track that never turned is the incident the ROE exists to
  -- prevent; one hit is enough, whoever's missile it was on our side.
  local handler = {}
  function handler:onEvent(event)
    if event.id ~= world.event.S_EVENT_HIT or event.target == nil then return end
    if event.initiator == nil or not event.initiator.getCoalition then return end
    if event.initiator:getCoalition() ~= side then return end
    local ok, group = pcall(function() return event.target:getGroup() end)
    if not ok or group == nil then return end
    local t = byGroup[group:getName()]
    if t == nil or t.state == "hostile" or t.fouled then return end
    t.fouled = true
    trigger.action.setUserFlag(flagFoul, 1)
    log(t, "hit while not hostile")
    say(t, "foul")
  end
  world.addEventHandler(handler)

  timer.scheduleFunction(poll, {}, timer.getTime() + 1)
end
