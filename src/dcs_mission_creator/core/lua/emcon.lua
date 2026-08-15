-- dcs_mission_creator: SAM EMCON reaction to anti-radiation missiles.
--
-- Rendered by `core/emcon.py` into a mission-start DoScript. It fills three
-- placeholders (named here without their underscores so this comment is not
-- itself substituted): SITES is one `{name=…, prob=…, …}` row per site, SIDE
-- the coalition constant the radio calls go to, COOLDOWN the seconds between
-- two calls of the same kind.
do
  local sites = {
__SITES__
  }
  local side = __SIDE__
  local cooldown = __COOLDOWN__
  local armNames = {"AGM_88", "AGM_45", "AGM_122", "ALARM", "Kh-25MP", "Kh-31P", "Kh-58"}
  local state = {}
  local lastDown, lastUp = -1e6, -1e6

  local function announce(text, sound, time, isDown)
    if isDown then
      if time - lastDown < cooldown then return end
      lastDown = time
    else
      if time - lastUp < cooldown then return end
      lastUp = time
    end
    if text then trigger.action.outTextForCoalition(side, text, 12) end
    if sound then trigger.action.outSoundForCoalition(side, sound) end
  end

  local function emissions(site, on)
    local g = Group.getByName(site.name)
    if not g or not g:isExist() then return false end
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
      announce(site.upText, site.upSound, time, false)
    end
    return nil
  end

  local function goDark(site, time)
    local down = site.downMin + math.random() * (site.downMax - site.downMin)
    local st = state[site.name]
    if st then
      -- Already dark: repeated fire keeps the crew off the air longer.
      st.wakeAt = math.max(st.wakeAt, time + down)
      return nil
    end
    if not emissions(site, false) then return nil end
    state[site.name] = {wakeAt = time + down}
    announce(site.downText, site.downSound, time, true)
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
      if g and g:isExist() then
        local units = g:getUnits()
        local u = units and units[1]
        if u then
          local p = u:getPoint()
          local dx, dz = sp.x - p.x, sp.z - p.z
          if math.sqrt(dx * dx + dz * dz) <= site.range and math.random() <= site.prob then
            local delay = site.delayMin + math.random() * (site.delayMax - site.delayMin)
            timer.scheduleFunction(goDark, site, now + delay)
          end
        end
      end
    end
  end
  world.addEventHandler(handler)
end
