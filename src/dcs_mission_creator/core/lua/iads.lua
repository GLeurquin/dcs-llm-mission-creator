-- dcs_mission_creator: build the IADS on Skynet, keep our own HARM model.
--
-- Rendered by `core/iads.py` into a mission-start DoScript that runs *after*
-- `mist_shim.lua` and `vendor/skynet-iads.lua`. Placeholders it fills (named
-- here without their underscores so this comment is not itself substituted):
-- SITES is one `{name=…, prob=…, …}` row per site, SIDE the coalition the radio
-- calls go to, SPACING the seconds between two consecutive calls, UPDATE the
-- IADS go-live cycle in seconds, IADSNAME the net's name in the log, DEBUG a
-- table of Skynet's own debug switches.
--
-- The split of responsibility, which is the whole point of this file:
--
--   Skynet owns *when a site radiates*. It knows each system's real envelopes,
--   analyses every launcher and radar against them, cues sites off whichever
--   radars are live, tracks ammunition, and degrades the net when links or
--   power go down. That is a lot of behaviour nobody should re-derive.
--
--   We own *what happens when somebody shoots at a site*. Skynet identifies the
--   missile in flight — over 800 kt, few flight-path changes — and darkens
--   radars ahead of its track. That gives a crew knowledge of a passive weapon
--   they have no way to have: an anti-radiation missile emits nothing, warns
--   nobody, and cannot be seen coming. What a crew can see is the *shooter*, so
--   below, a site reacts only to a launch it or its net could observe, and only
--   after the seconds it takes to call that down the net and act on it.
--   Skynet's own HARM detection is switched off at the bottom of this file.
do
  local sites = {
__SITES__
  }
  local side = __SIDE__
  local spacing = __SPACING__
  local updateInterval = __UPDATE__
  local iadsName = __IADSNAME__
  local debugSwitches = __DEBUG__

  local armNames = {"AGM_88", "AGM_45", "AGM_122", "ALARM", "Kh-25MP", "Kh-31P", "Kh-58"}

  -- ------------------------------------------------------------ radio calls --
  --
  -- One HARM shot puts several sites dark at once, so calls are *queued* and
  -- played `spacing` seconds apart rather than dropped: every site still gets
  -- its own call, they just do not talk over each other. The queue is capped so
  -- a busy net cannot back up a minute of stale traffic.
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

  -- ------------------------------------------------- what a site can observe --
  --
  -- Deliberately raw DCS rather than Skynet's element wrappers. Whether a crew
  -- saw a launch is this project's model, and keeping it off Skynet's internals
  -- means a Skynet upgrade cannot quietly change the answer.

  local function eye(p, h)
    return {x = p.x, y = p.y + h, z = p.z}
  end

  -- Terrain line of sight. The ground unit's origin is lifted to roughly
  -- antenna height or the trace starts inside the hill the site is parked on.
  local function seesFrom(p, target)
    return land.isVisible(eye(p, 8.0), eye(target, 2.0))
  end

  local function flat2(a, b)
    local dx, dz = a.x - b.x, a.z - b.z
    return math.sqrt(dx * dx + dz * dz)
  end

  -- The attributes DCS tags radar vehicles with (search, track/fire-control,
  -- EWR). A site whose radars are all dead is destroyed rather than
  -- suppressible: it has nothing to switch off and the crew making the call is
  -- gone. `Group.isExist()` stays true while one launcher stands, which is why
  -- the gate is a live radar unit and not a live group.
  local radarAttrs = {"SAM SR", "SAM TR", "EWR"}

  local function isRadar(u)
    for _, attr in ipairs(radarAttrs) do
      if u:hasAttribute(attr) then return true end
    end
    return false
  end

  -- Where the site sees from: a live radar if it has one, otherwise any live
  -- unit, so a group of a type DCS does not tag still has a viewpoint.
  -- Returns nil once nothing is left alive.
  local function eyePoint(name)
    local g = Group.getByName(name)
    if not g or not g:isExist() then return nil end
    local fallback = nil
    for _, u in ipairs(g:getUnits() or {}) do
      if u:isExist() and u:getLife() > 0 then
        if isRadar(u) then return u:getPoint() end
        if fallback == nil then fallback = u end
      end
    end
    return fallback and fallback:getPoint() or nil
  end

  local function hasLiveRadar(site)
    local g = Group.getByName(site.name)
    if not g or not g:isExist() then return false end
    local anyAlive = false
    for _, u in ipairs(g:getUnits() or {}) do
      if u:isExist() and u:getLife() > 0 then
        if isRadar(u) then return true end
        anyAlive = true
      end
    end
    -- Never had a radar-tagged unit at all: fall back to plain survival rather
    -- than declaring an untagged group permanently dead.
    if site.hadRadar then return false end
    return anyAlive
  end

  -- ---------------------------------------------------------- build the net --

  local iads = SkynetIADS:create(iadsName)

  if debugSwitches then
    local dbg = iads:getDebugSettings()
    for key, value in pairs(debugSwitches) do dbg[key] = value end
  end

  local ZONES = {
    kill = SkynetIADSAbstractRadarElement.GO_LIVE_WHEN_IN_KILL_ZONE,
    search = SkynetIADSAbstractRadarElement.GO_LIVE_WHEN_IN_SEARCH_RANGE,
  }
  local AUTONOMY = {
    dark = SkynetIADSAbstractRadarElement.AUTONOMOUS_STATE_DARK,
    ai = SkynetIADSAbstractRadarElement.AUTONOMOUS_STATE_DCS_AI,
  }

  -- Registered by exact name, never by prefix: the group names come from pydcs
  -- and the mission owns them, so there is nothing to pattern-match and no way
  -- for an unrelated group to be swept into the net by sharing a few letters.
  -- An EWR is a *unit* to Skynet and a SAM site is a *group* — different
  -- registration calls and different classes behind them.
  for _, site in ipairs(sites) do
    if site.ewUnit then
      iads:addEarlyWarningRadar(site.ewUnit)
      site.el = iads:getEarlyWarningRadarByUnitName(site.ewUnit)
    else
      iads:addSAMSite(site.name)
      site.el = iads:getSAMSiteByGroupName(site.name)
    end
    if site.el == nil then
      env.error("dcs_mission_creator IADS: Skynet would not take site " .. site.name)
    end
    site.hadRadar = hasLiveRadar(site)
  end

  for _, site in ipairs(sites) do
    local el = site.el
    if el then
      -- Cueing: Skynet sizes this off the system's own envelope, so the mission
      -- states a percentage of that reach rather than a distance it had to look
      -- up. Over 100% brings a long-range battery up before the target is in
      -- range, which is what a real one does.
      if site.golive then el:setGoLiveRangeInPercent(site.golive) end
      if site.zone and ZONES[site.zone] then el:setEngagementZone(ZONES[site.zone]) end
      if site.actAsEW then el:setActAsEW(true) end
      if site.autonomous and AUTONOMY[site.autonomous] then
        el:setAutonomousBehaviour(AUTONOMY[site.autonomous])
      end
      -- Skynet's missile-in-flight identification is off (see the bottom of
      -- this file), and this is the same statement in its own vocabulary: no
      -- element decides for itself that a contact is a HARM.
      el:setHARMDetectionChance(0)
    end
  end

  -- Point defence, wired after every element exists so the order sites appear
  -- in the table cannot matter.
  for _, site in ipairs(sites) do
    if site.el and site.pd then
      local guard = iads:getSAMSiteByGroupName(site.pd)
      if guard then
        site.el:addPointDefence(guard)
      else
        env.error("dcs_mission_creator IADS: no point defence site named " .. site.pd)
      end
    end
  end

  -- ------------------------------------------------------- coming on the air --
  --
  -- Skynet drives go-live, so this only decides what gets said about it. A site
  -- comes up and goes down constantly as targets come and go; almost all of
  -- that is not news.
  for _, site in ipairs(sites) do
    local el = site.el
    if el then
      -- An EWR is already live by now: `addEarlyWarningRadar` calls `goLive` on
      -- it during registration, before this wrapper exists. Seeding from the
      -- current state stops that first transition being announced later as
      -- though the site had just come up.
      site.everHot = (el.aiState == true)
      local goLive = el.goLive
      -- Set on the instance, so it shadows the class method for this element
      -- only and every internal `self:goLive()` goes through it.
      el.goLive = function(this, ...)
        local wasLive = (this.aiState == true)
        goLive(this, ...)
        if this.aiState == true and not wasLive then
          if site.wasSuppressed then
            -- Back on the air after being shot off it. That one is news.
            site.wasSuppressed = nil
            announce(site.upText, site.upSound, timer.getTime())
          elseif not site.everHot then
            -- First time up in the sortie. Silent by default: the player's RWR
            -- is the call, and a warning ahead of the strobe would give away a
            -- battery the briefing deliberately left off the map.
            announce(site.hotText, site.hotSound, timer.getTime())
          end
          site.everHot = true
        end
      end
    end
  end

  -- ------------------------------------------------------------- suppression --
  --
  -- Skynet's shutdown window is derived from the missile's time to impact and
  -- capped at 180 s past it, because it starts the clock when it sees the round
  -- coming. We start it when the crew acts on a launch call, and the window is
  -- the mission's own `shutdown_s` band — minutes, so a HARM buys the package a
  -- real working gap rather than a pause. The mechanism underneath is Skynet's:
  -- `harmSilenceID` is what blocks `goLive` until it clears, and
  -- `finishHarmDefence` is what releases the site, back to *cold* rather than
  -- hot — it re-radiates only if there is still something worth shooting at.
  local function suppress(site, now)
    local el = site.el
    if el == nil then return end
    local until_ = now + spread(site.downMin, site.downMax)
    -- Repeat fire keeps a crew off the air longer, never shorter.
    if el:isDefendingHARM() and site.darkUntil and site.darkUntil >= until_ then return end
    local wasLive = (el.aiState == true)
    site.darkUntil = until_

    -- Give the point defences their shot, the way Skynet's own HARM path does.
    if #el:getPointDefences() > 0 then el:pointDefencesGoLive() end

    el:finishHarmDefence(el)
    el.harmShutdownTime = until_ - now
    el.harmSilenceID = mist.scheduleFunction(
      SkynetIADSAbstractRadarElement.finishHarmDefence, {el}, until_, 1)
    el:goDark()

    if wasLive then
      site.wasSuppressed = true
      announce(site.downText, site.downSound, now)
    end
  end

  -- ---------------------------------------------------- seeing the launch ---

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

  -- A site that saw nothing itself is not necessarily deaf — this is a net, and
  -- the call travels. `site.relay` is how much of the crew's own reaction chance
  -- survives being told second-hand by whichever site did see it. That relay is
  -- what keeps a masked battery behind a live EWR chain dangerous, and what
  -- makes killing the search radars first pay off twice.
  local function shotChance(site, p, sp, observed)
    if seesFrom(p, sp) then return site.prob end
    if observed then return site.prob * site.relay end
    return 0.0
  end

  local handler = {}
  function handler:onEvent(event)
    if event == nil or event.id ~= world.event.S_EVENT_SHOT then return end
    if not isArm(event.weapon) then return end
    local shooter = event.initiator
    if shooter == nil or not shooter:isExist() then return end
    local sp = shooter:getPoint()
    local now = timer.getTime()

    -- Who is in reach of the launch, and did anybody see it? Gathered first,
    -- because whether *anyone* saw the shot decides what the rest are told.
    local watching, observed = {}, false
    for _, site in ipairs(sites) do
      local el = site.el
      -- A site that is cold is not being shot at: no radar of its own is up, so
      -- an anti-radiation round was not aimed at it and its crew has no reason
      -- to think otherwise. One already suppressed still has its crew, and
      -- repeat fire keeps them down longer. A dead site is neither.
      if el and (el.aiState == true or el:isDefendingHARM()) and hasLiveRadar(site) then
        local p = eyePoint(site.name)
        if p and flat2(sp, p) <= site.range then
          watching[#watching + 1] = {site = site, p = p}
          if not observed and seesFrom(p, sp) then observed = true end
        end
      end
    end

    for _, w in ipairs(watching) do
      if math.random() <= shotChance(w.site, w.p, sp, observed) then
        -- Drawn per site per shot, and deliberately of the same order as a
        -- HARM's time of flight: nobody gets a launch warning, so the shooter's
        -- range at launch is what decides whether the missile arrives before
        -- the transmitter dies.
        local site = w.site
        timer.scheduleFunction(function(_, when)
          suppress(site, when)
          return nil
        end, {}, now + spread(site.delayMin, site.delayMax))
      end
    end
  end
  world.addEventHandler(handler)

  -- --------------------------------------------------------------- activate --

  if updateInterval then iads:setUpdateInterval(updateInterval) end
  iads:activate()

  -- Switched off *after* activate, because activate is what starts the contact
  -- cycle that would otherwise run it. This is the one Skynet behaviour this
  -- project rejects rather than reuses: identifying an anti-radiation missile
  -- from its flight profile hands a crew knowledge of a passive weapon they
  -- cannot have. With this stubbed, no contact is ever flagged as a HARM, so
  -- every element's own `evaluateIfTargetsContainHARMs` finds nothing and the
  -- only thing that darkens a site for anti-radiation fire is the handler above.
  iads.harmDetection.evaluateContacts = function() return nil end

  -- Kept reachable for a mission that wants to reach into the net directly (a
  -- jammer, a command centre, a trigger that cuts a connection node). Global on
  -- purpose: separate DoScript actions do not share locals.
  dcsmcIADS = iads
end
