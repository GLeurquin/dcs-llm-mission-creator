-- dcs_mission_creator: SAM EMCON reaction to anti-radiation missiles.
--
-- Rendered by `core/emcon.py` into a mission-start DoScript. It fills three
-- placeholders (named here without their underscores so this comment is not
-- itself substituted): SITES is one `{name=…, prob=…, …}` row per site, SIDE
-- the coalition constant the radio calls go to, SPACING the seconds left
-- between two consecutive radio calls.
do
  local sites = {
__SITES__
  }
  local side = __SIDE__
  local spacing = __SPACING__
  local armNames = {"AGM_88", "AGM_45", "AGM_122", "ALARM", "Kh-25MP", "Kh-31P", "Kh-58"}
  local state = {}

  -- One HARM shot puts several sites dark at once, so calls are *queued* and
  -- played `spacing` seconds apart rather than dropped: every site still gets
  -- its own call, they just do not talk over each other. The queue is capped
  -- so a busy net cannot back up a minute of stale traffic.
  local queue, draining = {}, false

  local function playNext(_, time)
    local item = table.remove(queue, 1)
    if item == nil then
      draining = false
      return nil
    end
    if item.text then trigger.action.outTextForCoalition(side, item.text, 12) end
    if item.sound then trigger.action.outSoundForCoalition(side, item.sound) end
    return time + spacing
  end

  local function announce(text, sound, time)
    if text == nil and sound == nil then return end
    -- Sites that share wording (the EWR pair) say it once, not once each.
    for _, q in ipairs(queue) do
      if q.text == text and q.sound == sound then return end
    end
    if #queue >= 8 then table.remove(queue, 1) end
    queue[#queue + 1] = {text = text, sound = sound}
    if not draining then
      draining = true
      timer.scheduleFunction(playNext, {}, time + 0.1)
    end
  end

  -- Reaction time is not a flat coin flip across the band: the middle is the
  -- common case, and both a snap reaction and a badly slow one are rare.
  -- Averaging two uniform draws gives that triangular shape in one line. The
  -- same spread reads right for how long a crew then stays off the air.
  local function spread(lo, hi)
    return lo + (math.random() + math.random()) * 0.5 * (hi - lo)
  end

  -- A site that has lost its radars is not dark, it is dead — it has nothing
  -- left to switch off and the crew making the call is gone. `Group.isExist()`
  -- stays true while a single launcher or the command post survives, so the one
  -- thing the calls have to be gated on is a live emitter. These are the
  -- attributes DCS tags radar vehicles with (search, track/fire-control, EWR).
  local radarAttrs = {"SAM SR", "SAM TR", "EWR"}

  local function liveRadars(name)
    local g = Group.getByName(name)
    if not g or not g:isExist() then return 0 end
    local n = 0
    for _, u in ipairs(g:getUnits() or {}) do
      if u:isExist() and u:getLife() > 0 then
        for _, attr in ipairs(radarAttrs) do
          if u:hasAttribute(attr) then
            n = n + 1
            break
          end
        end
      end
    end
    return n
  end

  -- `site.radars` remembers the most radars this site was ever seen with, so a
  -- group activated after mission start is counted the first time it is looked
  -- at rather than mistaken for a wreck. A site that never had a radar-tagged
  -- unit at all (a hand-built group of some type DCS does not tag) falls back to
  -- plain group existence, so it still reacts instead of going silent forever.
  local function radiating(site)
    local n = liveRadars(site.name)
    if n > (site.radars or 0) then site.radars = n end
    if n > 0 then return true end
    if (site.radars or 0) > 0 then return false end
    local g = Group.getByName(site.name)
    return g ~= nil and g:isExist()
  end

  -- Counted here, at mission start, while every site is still intact: a radar
  -- killed by something other than a HARM, before the first anti-radiation shot
  -- of the mission, has to read as dead rather than as one of those untagged
  -- groups. Late-activated sites do not exist yet and are picked up lazily above.
  for _, site in ipairs(sites) do
    site.radars = liveRadars(site.name)
  end

  local function emissions(site, on)
    local g = Group.getByName(site.name)
    if not g or not g:isExist() then return false end
    if not radiating(site) then return false end
    local c = g:getController()
    if on then
      c:setOption(AI.Option.Ground.id.ALARM_STATE, AI.Option.Ground.val.ALARM_STATE.RED)
      c:setOption(AI.Option.Ground.id.ROE, AI.Option.Ground.val.ROE.OPEN_FIRE)
    else
      c:setOption(AI.Option.Ground.id.ALARM_STATE, AI.Option.Ground.val.ALARM_STATE.GREEN)
      c:setOption(AI.Option.Ground.id.ROE, AI.Option.Ground.val.ROE.WEAPON_HOLD)
    end
    return true
  end

  local function wakeUp(site, time)
    local st = state[site.name]
    -- A later launch pushed the wake-up back; sleep on until then.
    if st and st.wakeAt > time + 0.5 then return st.wakeAt end
    state[site.name] = nil
    if emissions(site, true) then
      announce(site.upText, site.upSound, time)
    end
    return nil
  end

  local function goDark(site, time)
    local down = spread(site.downMin, site.downMax)
    local st = state[site.name]
    if st then
      -- Already dark: repeated fire keeps the crew off the air longer.
      st.wakeAt = math.max(st.wakeAt, time + down)
      return nil
    end
    if not emissions(site, false) then return nil end
    state[site.name] = {wakeAt = time + down}
    announce(site.downText, site.downSound, time)
    timer.scheduleFunction(wakeUp, site, time + down)
    return nil
  end

  local function isArm(w)
    if w == nil then return false end
    local ok, desc = pcall(function() return w:getDesc() end)
    if not ok or desc == nil then return false end
    if Weapon and Weapon.GuidanceType and desc.guidance == Weapon.GuidanceType.RADAR_PASSIVE then
      return true
    end
    local tn = desc.typeName or ""
    for _, pat in ipairs(armNames) do
      if string.find(tn, pat, 1, true) then return true end
    end
    return false
  end

  local handler = {}
  function handler:onEvent(event)
    if event == nil or event.id ~= world.event.S_EVENT_SHOT then return end
    if not isArm(event.weapon) then return end
    local shooter = event.initiator
    if shooter == nil or not shooter:isExist() then return end
    local sp = shooter:getPoint()
    local now = timer.getTime()
    for _, site in ipairs(sites) do
      local g = Group.getByName(site.name)
      if g and g:isExist() and radiating(site) then
        local units = g:getUnits()
        local u = units and units[1]
        if u then
          local p = u:getPoint()
          local dx, dz = sp.x - p.x, sp.z - p.z
          if math.sqrt(dx * dx + dz * dz) <= site.range and math.random() <= site.prob then
            -- Drawn per site per shot, and deliberately of the same order as a
            -- HARM's time of flight: the missile's range at launch is what
            -- decides whether it arrives before the transmitter dies.
            timer.scheduleFunction(goDark, site, now + spread(site.delayMin, site.delayMax))
          end
        end
      end
    end
  end
  world.addEventHandler(handler)
end
