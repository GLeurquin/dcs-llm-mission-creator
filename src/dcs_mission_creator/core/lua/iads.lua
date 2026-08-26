-- dcs_mission_creator: build the IADS on Skynet, keep our own HARM model.
--
-- Rendered by `core/iads.py` into a mission-start DoScript that runs *after*
-- `mist_shim.lua` and `vendor/skynet-iads.lua`. Placeholders it fills (named
-- here without their underscores so this comment is not itself substituted):
-- SITES is one `{name=…, prob=…, jockey=…, …}` row per site, LISTENERS one row
-- per friendly collector that could hear a radar change state, SIDE the coalition
-- the radio calls go to, SPACING the seconds between two consecutive calls,
-- UPDATE the IADS go-live cycle in seconds, IADSNAME the net's name in the log,
-- DEBUG a table of Skynet's own debug switches, TRACE whether this file logs its
-- own decisions, ALERT how long an observed launch keeps the net on notice.
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
  -- Who could hear a radar start or stop: see `heardBy` below. Empty is a valid
  -- net — it just does not report anything.
  local listeners = {
__LISTENERS__
  }
  local side = __SIDE__
  local spacing = __SPACING__
  local updateInterval = __UPDATE__
  local iadsName = __IADSNAME__
  local debugSwitches = __DEBUG__
  local tracing = __TRACE__
  local alertWindow = __ALERT__

  -- ---------------------------------------------------------------- tracing --
  --
  -- Skynet's debug switches say what the *framework* decided. Nothing says what
  -- this file decided, and that is the half a mission tunes: whether a crew was
  -- in a position to see the launch, what its reaction rolled against, how long
  -- the window came out and where the battery drove. Every line goes to
  -- `dcs.log` only — `grep 'IADS/'` reads a sortie back — and none of it reaches
  -- the player's screen: a trace is for whoever is balancing the net, and a SAM
  -- announcing its own suppression logic would give away every battery on it.
  --
  -- Tracing must not change what happens. In particular the reaction roll below
  -- is drawn whether or not anyone is reading it, so a traced sortie and a quiet
  -- one make the same decisions from the same seed.
  local function num(v)
    if v == nil then return "nil" end
    return string.format("%.0f", v)
  end

  local function trace(msg)
    if not tracing then return end
    env.info(string.format("IADS/%s t+%ss: %s", iadsName, num(timer.getTime()), msg))
  end

  local armNames = {"AGM_88", "AGM_45", "AGM_122", "ALARM", "Kh-25MP", "Kh-31P", "Kh-58"}

  -- Shoot and scoot. This is a *hasty* displacement over a few hundred metres,
  -- not a road march: a Kub TELAR is good for 40 km/h cross-country and the
  -- crew driving it off an aimpoint is not saving the transmission, so the
  -- commanded speed is the dash and not the cruise. It stays well under what
  -- the vehicle can do because a DCS group moves at its slowest member and a
  -- battery brings trucks. Measured against a HARM's time of flight, the older
  -- 5.5 m/s meant a shot from inside the missile engagement zone was never
  -- survivable at all — the crew reacted and then moved twelve metres.
  --
  -- A hop shorter than the minimum is not a displacement — it is the same patch
  -- of ground with the vehicle parked differently, and a HARM's terminal error
  -- covers it.
  local JOCKEY_SPEED_MS = 9.0
  local JOCKEY_MIN_HOP_M = 60.0
  local JOCKEY_TRIES = 8

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

  -- Where the group as a whole is. The leader is what a route order steers, and
  -- it is the unit `Mission.vehicle_group` put on the position the mission drew
  -- its ring around — so this is the reference a displacement is measured from.
  local function anchorPoint(name)
    local g = Group.getByName(name)
    if not g or not g:isExist() then return nil end
    local units = g:getUnits() or {}
    return units[1] and units[1]:getPoint() or nil
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

  -- ------------------------------------------ who could know a radar changed --
  --
  -- An emissions change is intel, and intel needs a collector. "That site has
  -- ceased emissions" is a claim only something with a receiver on it can make:
  -- an ELINT platform on a track, an AWACS with ESM, a ground collection site,
  -- an aircraft watching the strobe drop off its own RWR. With nothing of ours
  -- in a position to hear the radar the call is not made — otherwise the mission
  -- is reading its own trigger state out to the player, which is the one thing
  -- its briefings are not allowed to do.
  --
  -- The gate is geometry: alive, inside the collector's reach, and terrain line
  -- of sight to the emitter. At the reach `core/iads.py` hands down — a passive
  -- receiver against a megawatt search radar is horizon-limited, not
  -- power-limited — the conditions that actually bite are the other two, and
  -- both are live: a collector that is shot down, has not spawned (a client slot
  -- nobody is in) or has gone home stops carrying the reporting.
  local function heardBy(site)
    -- The emitter's own position, so a site that has displaced is judged from
    -- where its radar is now.
    local p = eyePoint(site.name)
    if p == nil then return nil end
    for _, l in ipairs(listeners) do
      local g = Group.getByName(l.name)
      if g and g:isExist() then
        for _, u in ipairs(g:getUnits() or {}) do
          if u:isExist() and u:getLife() > 0 then
            local from = u:getPoint()
            local d = flat2(from, p)
            if d <= l.range and land.isVisible(eye(from, 2.0), eye(p, 8.0)) then
              return l.label, d
            end
          end
        end
      end
    end
    return nil
  end

  -- Every radio call about a radar goes through here rather than straight to the
  -- queue, because "could anyone know this" is the same question whether a site
  -- went dark, came back up, or came up for the first time.
  local function report(site, text, sound, now, what)
    if text == nil and sound == nil then return end
    local by, range = heardBy(site)
    if by == nil then
      trace(string.format("%s %s, and nothing of ours could hear it — no call",
        site.name, what))
      return
    end
    trace(string.format("%s %s, heard by %s at %s m — calling it",
      site.name, what, by, num(range)))
    announce(text, sound, now)
  end

  -- The net's memory of a launch it saw, and the hook a site coming on the air
  -- goes through because of it. Both are declared here and filled in further
  -- down: the go-live wrapper is installed before `suppress` exists, so the
  -- reaction has to reach it as an upvalue rather than by name.
  local alert, shotSeq, considerAlert, jockey = nil, 0, nil, nil

  -- ------------------------------------------------- holding the emissions --
  --
  -- Skynet will not take a radar off the air while it has a target and ammunition
  -- — its `goDark` refuses — so the framework's own "stay dark regardless"
  -- mechanism is what both of this file's shutdowns go through. `harmSilenceID`
  -- is what blocks `goLive` until it clears and `finishHarmDefence` is what
  -- releases the site, back to *cold* rather than hot.
  local function holdSilence(site, el, until_, now)
    el:finishHarmDefence(el)
    el.harmShutdownTime = until_ - now
    el.harmSilenceID = mist.scheduleFunction(
      SkynetIADSAbstractRadarElement.finishHarmDefence, {el}, until_, 1)
    site.darkUntil = until_
    el:goDark()
  end

  -- ---------------------------------------------------- emission discipline --
  --
  -- "Radars were also forced to operate for only 20 seconds or less to avoid
  -- destruction by HARMs" — Desert Storm, and again over Yugoslavia in 1999,
  -- where the rule was never to radiate from one position for more than about
  -- forty seconds. It is the discipline rather than the reaction that kept
  -- batteries alive: a crew that is already off the air when the round arrives
  -- did not have to out-react anything.
  --
  -- So a site radiates in bursts, and the length of the burst is a statement
  -- about the crew: `core/iads.py` reads it off the group's own DCS skill, so a
  -- conscript battery sits on the air and a drilled one works in twenty-second
  -- looks. What the limit does *not* do is refuse an engagement — Dani's own
  -- radar stayed up the extra twenty seconds to finish the shot that downed an
  -- F-117 — so the clock is held while there are missiles in flight or a target
  -- inside the launch envelope. The burst limit is about sitting there
  -- illuminating for a HARM shooter's benefit, not about declining to shoot.
  local DISCIPLINE_RECHECK_S = 5.0

  -- Deliberately *not* `isTargetInRange`, which is Skynet's go-live test and so
  -- carries `go_live_percent` in it: at 150 % that answers "is this site cued",
  -- which is true of everything it can see and would hold the clock for ever. The
  -- question here is the different one of whether the battery could fire right
  -- now, so it is the launchers' own envelope — range, firing altitude and
  -- something left to shoot — with no cue factor applied.
  local function canShootNow(el, contact)
    for _, launcher in ipairs(el:getLaunchers()) do
      if launcher:isExist()
        and (launcher:getRemainingNumberOfMissiles() > 0
             or launcher:getRemainingNumberOfShells() > 0)
        and launcher:getRange() >= launcher:getDistance(contact)
        and launcher:isWithinFiringHeight(contact) then
        return true
      end
    end
    return false
  end

  local function engaging(el)
    if el:hasMissilesInFlight() then return true end
    for _, contact in ipairs(el:getDetectedTargets()) do
      if canShootNow(el, contact) then return true end
    end
    return false
  end

  local function discipline(site, burst, now)
    local el = site.el
    -- Off the air already, or this timer belongs to an earlier burst: the
    -- go-live that started the current one armed its own.
    if el == nil or el.aiState ~= true or site.burst ~= burst then return nil end
    if engaging(el) then return now + DISCIPLINE_RECHECK_S end
    local pause = spread(site.pauseMin, site.pauseMax)
    trace(string.format(
      "%s has held its look long enough — off the air for %ss by its own"
      .. " discipline", site.name, num(pause)))
    -- Not `suppressing`: this is not a reaction, so it says nothing on the radio
    -- and the go-dark hook is free to relocate it, which is the other half of
    -- the same doctrine.
    site.quieting = true
    holdSilence(site, el, now + pause, now)
    site.quieting = nil
    return nil
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
    trace(string.format(
      "registered %s as %s%s, radar-tagged units: %s",
      site.name,
      site.ewUnit and ("early-warning unit " .. site.ewUnit) or "SAM group",
      site.el == nil and " — REFUSED by Skynet" or "",
      tostring(site.hadRadar)))
    -- Captured once, at setup. Every displacement is measured from here rather
    -- than from wherever the last one ended, so repeat HARM fire cannot walk a
    -- battery out of the ring the briefing drew around its start point.
    site.home = anchorPoint(site.name)
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
      trace(string.format(
        "%s cue at %s%% of own reach, %s zone, actAsEW=%s, autonomous=%s, "
        .. "react to a launch within %s m at p=%.2f (relayed %.2f), "
        .. "recognition %s-%s s, dark %s-%s s, displaces %s m, "
        .. "relocates after %ss on the air, looks of %s-%ss then %s-%ss quiet",
        site.name, site.golive == nil and "default" or num(site.golive),
        site.zone or "default", tostring(site.actAsEW == true),
        site.autonomous or "default", num(site.range), site.prob, site.relay,
        num(site.delayMin), num(site.delayMax), num(site.downMin), num(site.downMax),
        num(site.jockey or 0), num(site.scootAfter or 0),
        num(site.emitMin), num(site.emitMax), num(site.pauseMin), num(site.pauseMax)))
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
          local reason = " again"
          if site.wasSuppressed then
            -- Back on the air after being shot off it. That one is news.
            site.wasSuppressed = nil
            reason = " again after being shot off the air"
            report(site, site.upText, site.upSound, timer.getTime(),
              "came back on the air")
          elseif not site.everHot then
            reason = " for the first time this sortie"
            -- First time up in the sortie. Silent by default: the player's RWR
            -- is the call, and a warning ahead of the strobe would give away a
            -- battery the briefing deliberately left off the map.
            report(site, site.hotText, site.hotSound, timer.getTime(),
              "came up for the first time")
          end
          site.everHot = true
          site.silenceLogged = nil
          site.liveSince = timer.getTime()
          if site.emitMax and site.emitMax > 0 then
            local burst = spread(site.emitMin, site.emitMax)
            site.burst = (site.burst or 0) + 1
            local mine = site.burst
            trace(string.format("%s intends a look of at most %ss",
              site.name, num(burst)))
            timer.scheduleFunction(function(_, when)
              return discipline(site, mine, when)
            end, {}, timer.getTime() + burst)
          end
          trace(site.name .. " is radiating" .. reason)
          -- Coming up is also how a site finds out it is under attack: there may
          -- be a round already in the air, aimed at whatever radiates next.
          considerAlert(site, timer.getTime())
        elseif this.aiState ~= true and this:isDefendingHARM() and not site.silenceLogged then
          -- Skynet refuses the transition while the site is HARM-silenced. Said
          -- once per window, not once per go-live cycle: the framework asks
          -- every `update_interval_s` for as long as there is a target.
          site.silenceLogged = true
          trace(site.name .. " wanted to come up and is still off the air")
        end
      end

      local goDark = el.goDark
      el.goDark = function(this, ...)
        local wasLive = (this.aiState == true)
        goDark(this, ...)
        if wasLive and this.aiState ~= true then
          -- Time on the air is what compromises a position, so it is counted
          -- here rather than at the shot. Cued sites flap on and off at the
          -- go-live cycle, which is why this accumulates instead of measuring
          -- one stretch.
          if site.liveSince then
            site.airtime = (site.airtime or 0) + (timer.getTime() - site.liveSince)
            site.liveSince = nil
          end
          trace(site.name .. " has gone dark" .. (site.suppressing
            and " — reacting to an anti-radiation launch"
            or site.quieting and " — holding emissions"
            or " — nothing left in range"))

          -- Going off the air is not what saves a battery from a *pre-planned*
          -- shot, and that is the shot a competent player takes: an
          -- anti-radiation round in POS or EOM mode is aimed at a place, so it
          -- flies to the coordinates whether anything is still radiating there
          -- or not. What defeats it is the coordinates being stale — which
          -- means the hop that matters happened before the launch, not after
          -- it. This is that hop: the site has been emitting, it must assume it
          -- was fixed while it did, and now that it is quiet it moves. It is
          -- also the doctrine the vehicle exists for; a battery that only ever
          -- displaces once a missile is already in the air is not shooting and
          -- scooting, it is dodging.
          --
          -- `site.suppressing` excludes the anti-radiation path, which does its
          -- own hop with its own timing.
          if not site.suppressing and site.jockey and site.jockey > 0
             and site.scootAfter and site.scootAfter > 0
             and (site.airtime or 0) >= site.scootAfter then
            trace(string.format(
              "%s has radiated %ss since it last moved — position compromised,"
              .. " displacing", site.name, num(site.airtime)))
            site.airtime = 0
            jockey(site)
          end
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
  -- ------------------------------------------------------- shoot and scoot --
  --
  -- Ceasing to radiate saves the battery, not the vehicle: an anti-radiation
  -- round remembers where the emitter was and keeps flying to that point.
  -- Skynet answers with `setOnOff(false)` in its own dark path, which is a
  -- workaround for a DCS multiplayer quirk rather than a tactic. A site that
  -- can drive has the real answer, so for those the AI goes back on and the
  -- battery leaves the point the missile was aimed at.
  --
  -- It does not defeat the missile — it grades the duel. The hop begins when
  -- the crew reacts, which is `delay_s` after the launch, so a shot from 40 km
  -- arrives on ground the battery left and one from 15 km arrives before it has
  -- moved at all. Which system may do this at all is decided in `core/iads.py`;
  -- here `site.jockey` is a distance, and zero means a prepared site that fires
  -- from revetments and has nowhere to go.
  local function onLand(x, z)
    local surface = land.getSurfaceType({x = x, y = z})
    return surface == land.SurfaceType.LAND or surface == land.SurfaceType.ROAD
  end

  jockey = function(site)
    if not site.jockey or site.jockey <= 0 then
      trace(site.name .. " fires from prepared positions and stays put")
      return
    end
    if site.home == nil then return end
    -- The gate is the same as the one on suppression: a site whose radars are
    -- gone is destroyed, and a wreck does not displace.
    if not hasLiveRadar(site) then return end
    local group = Group.getByName(site.name)
    local from = anchorPoint(site.name)
    if group == nil or from == nil then return end

    -- Uniform over the disc, not over the radius: sqrt keeps the draw from
    -- piling up near the centre, where the hop would be too short to matter.
    local dest = nil
    for _ = 1, JOCKEY_TRIES do
      local bearing = math.random() * 2.0 * math.pi
      local radius = site.jockey * math.sqrt(math.random())
      local cand = {x = site.home.x + radius * math.cos(bearing),
                    y = 0,
                    z = site.home.z + radius * math.sin(bearing)}
      if flat2(cand, from) >= JOCKEY_MIN_HOP_M and onLand(cand.x, cand.z) then
        dest = cand
        break
      end
    end
    -- A site on a spit of land or against a cliff has nowhere to go. That is a
    -- fact about where the mission put it, not an error.
    if dest == nil then
      trace(string.format(
        "%s found nowhere to displace to inside %s m of its start point",
        site.name, num(site.jockey)))
      return
    end
    trace(string.format(
      "%s displacing to %s m from its start point (%s m from where it was"
      .. " standing) at %s m/s",
      site.name, num(flat2(dest, site.home)), num(flat2(dest, from)),
      string.format("%.1f", JOCKEY_SPEED_MS)))

    -- `y` on a ground route point is the world *z*: north-east, not altitude.
    local function wp(p)
      return {
        type = "Turning Point",
        action = "Off Road",
        x = p.x,
        y = p.z,
        speed = JOCKEY_SPEED_MS,
        speed_locked = true,
        ETA = 0,
        ETA_locked = false,
        formation_template = "",
        task = {id = "ComboTask", params = {tasks = {}}},
      }
    end

    local cont = group:getController()
    -- goDark has just cut the group's AI, and a group with its AI off does not
    -- drive. Handing it back is what makes the displacement possible, and it is
    -- also why `core/iads.py` refuses to let an optically guided launcher into
    -- a site that displaces: it would go on fighting from here.
    cont:setOnOff(true)
    -- And take it out of the firing posture, which is the other half of being
    -- able to drive at all. Skynet sets ALARM_STATE RED when a site goes live
    -- and nothing ever clears it, so a battery told to displace is still
    -- deployed for action — and DCS will not move, or will barely move, a
    -- vehicle whose group is combat-ready: a stowed radar is what travelling
    -- costs, and the SA-6's own STR and the SA-8 are exactly the types that
    -- stow one. Green is also what a crew packing up to run actually does, and
    -- Skynet sets RED again the next time it brings the site up, so there is
    -- nothing to restore here.
    cont:setOption(AI.Option.Ground.id.ALARM_STATE,
                   AI.Option.Ground.val.ALARM_STATE.GREEN)
    -- Two points, the first being where the group is standing. A route that
    -- holds the destination alone is the shape every DCS framework avoids: with
    -- nothing to leave from, the order can be taken and then quietly ignored.
    cont:setTask({
      id = "Mission",
      params = {route = {points = {wp(from), wp(dest)}}},
    })
  end

  local function suppress(site, now)
    local el = site.el
    if el == nil then return end
    local until_ = now + spread(site.downMin, site.downMax)
    -- Repeat fire keeps a crew off the air longer, never shorter.
    if el:isDefendingHARM() and site.darkUntil and site.darkUntil >= until_ then
      trace(string.format(
        "%s already off the air until t+%ss, longer than this shot's %ss",
        site.name, num(site.darkUntil), num(until_ - now)))
      return
    end
    local wasLive = (el.aiState == true)

    trace(string.format(
      "%s reacting: off the air for %ss (until t+%ss), was %s, %s point defence(s)",
      site.name, num(until_ - now), num(until_),
      wasLive and "radiating" or "already cold", num(#el:getPointDefences())))

    -- Give the point defences their shot, the way Skynet's own HARM path does.
    if #el:getPointDefences() > 0 then el:pointDefencesGoLive() end

    site.suppressing = true
    holdSilence(site, el, until_, now)
    site.suppressing = nil
    jockey(site)

    -- Scheduled only when tracing, and guarded on the window it was queued for,
    -- so repeat fire that pushed the release out does not report an early one.
    if tracing then
      timer.scheduleFunction(function(_, when)
        if site.darkUntil and when >= site.darkUntil - 0.1 then
          trace(site.name .. " is released, cold — it comes back up only if there"
            .. " is still something to shoot at")
        end
        return nil
      end, {}, until_ + 0.2)
    end

    if wasLive then
      site.wasSuppressed = true
      report(site, site.downText, site.downSound, now, "went off the air")
    end
  end

  -- ---------------------------------------------------- seeing the launch ---

  -- Returns whether the round is an anti-radiation weapon, and its type name so
  -- the trace can say which one it was.
  local function isArm(w)
    if w == nil then return false, "nil" end
    local ok, desc = pcall(function() return w:getDesc() end)
    if not ok or desc == nil then return false, "unknown" end
    local tn = desc.typeName or ""
    if Weapon and Weapon.GuidanceType and desc.guidance == Weapon.GuidanceType.RADAR_PASSIVE then
      return true, tn
    end
    for _, pat in ipairs(armNames) do
      if string.find(tn, pat, 1, true) then return true, tn end
    end
    return false, tn
  end

  -- A site that saw nothing itself is not necessarily deaf — this is a net, and
  -- the call travels. `site.relay` is how much of the crew's own reaction chance
  -- survives being told second-hand by whichever site did see it. That relay is
  -- what keeps a masked battery behind a live EWR chain dangerous, and what
  -- makes killing the search radars first pay off twice.
  local function shotChance(site, los, observed)
    if los then return site.prob end
    if observed then return site.prob * site.relay end
    return 0.0
  end

  -- Recognition is not one number for every shot, and treating it as one was
  -- wrong in the crew's favour nowhere and against it at close range. A launch a
  -- few kilometres off is a rocket motor and a smoke trail in plain sight, and
  -- the historical trigger was looser still: in the Gulf a *bogus* "Magnum" call
  -- on the radio was often enough to make operators power down, so crews were
  -- acting on the suspicion of a shot rather than on the sight of one. A launch
  -- at the edge of the net's reach is the opposite — a report that has to be
  -- made, believed and passed along.
  --
  -- So the band the mission states is the band at that outer edge, and it
  -- tightens towards `RECOGNITION_NEAR` of itself as the launch gets closer,
  -- never below the floor: somebody still has to look up, decide, and reach the
  -- switch. A launch the site could not see itself keeps the slower reading even
  -- when it was close, because the crew is being told rather than looking.
  local RECOGNITION_NEAR = 0.45
  local RECOGNITION_FLOOR_S = 6.0
  local RELAYED_DELAY_MULT = 1.3

  local function recognition(site, dist, los)
    local reach = math.max(1.0, site.range)
    local f = RECOGNITION_NEAR
      + (1.0 - RECOGNITION_NEAR) * math.min(1.0, dist / reach)
    if not los then f = f * RELAYED_DELAY_MULT end
    local lo = math.max(RECOGNITION_FLOOR_S, site.delayMin * f)
    local hi = math.max(lo + 1.0, site.delayMax * f)
    -- Same two draws as the unscaled band, so the random stream is unchanged.
    return spread(lo, hi), lo, hi
  end

  -- ------------------------------------------ a round already in the air ---
  --
  -- Everything above decides the reaction at the instant of the shot, which
  -- leaves the standard tactic against a dark net unanswered: shoot first — a
  -- HARM in POS or EOM mode is aimed at a *place*, not at an emitter — and let
  -- the round arrive on whatever comes up. A battery that was cold at launch is
  -- in nobody's reaction, so it used to come on the air into a missile already
  -- on its way and die no matter how good its crew was. That made the pre-emptive
  -- shot a free kill, which is the one outcome this model exists to argue with.
  --
  -- So an observed launch leaves the net on notice for `alertWindow` seconds,
  -- the order of a HARM's time of flight. A site coming up inside that window is
  -- told about the shot rather than seeing it — it was not radiating and had
  -- nothing pointed at the launch — so the chance is its `relay` share of its
  -- own `probability`, and the recognition delay runs from the moment it came up
  -- rather than from the launch. One roll per site per shot either way: the
  -- handler below stamps every site it evaluated with the shot's id, so a site
  -- that already rolled at launch does not roll again on its way up.
  considerAlert = function(site, now)
    if alert == nil or (now - alert.time) > alertWindow then return end
    if site.lastShot == alert.id then return end
    site.lastShot = alert.id
    local p = eyePoint(site.name)
    if p == nil then return end
    local age, reach = now - alert.time, flat2(alert.point, p)
    if reach > site.range then
      trace(string.format(
        "%s came up %ss after a launch %s m away, past its %s m reach — nobody"
        .. " told it", site.name, num(age), num(reach), num(site.range)))
      return
    end
    local chance = site.prob * site.relay
    local roll = math.random()
    if roll > chance then
      trace(string.format(
        "%s came up %ss into a launch and does not act on it (rolled %.2f"
        .. " against %.2f)", site.name, num(age), roll, chance))
      return
    end
    -- Told, not seen: it was not on the air when the round left the rail.
    local delay = recognition(site, reach, false)
    trace(string.format(
      "%s came up %ss into a launch it was warned of and acts on it (rolled"
      .. " %.2f against %.2f), radar off in %ss",
      site.name, num(age), roll, chance, num(delay)))
    timer.scheduleFunction(function(_, when)
      suppress(site, when)
      return nil
    end, {}, now + delay)
  end

  local handler = {}
  function handler:onEvent(event)
    if event == nil or event.id ~= world.event.S_EVENT_SHOT then return end
    local arm, armType = isArm(event.weapon)
    if not arm then return end
    local shooter = event.initiator
    if shooter == nil or not shooter:isExist() then return end
    local sp = shooter:getPoint()
    local now = timer.getTime()
    trace(string.format("anti-radiation launch (%s) by %s at %s/%s, %s m",
      armType, shooter.getName and shooter:getName() or "?",
      num(sp.x), num(sp.z), num(sp.y)))

    -- Who is in reach of the launch, and did anybody see it? Gathered first,
    -- because whether *anyone* saw the shot decides what the rest are told.
    local watching, observed = {}, false
    for _, site in ipairs(sites) do
      local el = site.el
      -- A site that is cold is not being shot at: no radar of its own is up, so
      -- an anti-radiation round was not aimed at it and its crew has no reason
      -- to think otherwise. One already suppressed still has its crew, and
      -- repeat fire keeps them down longer. A dead site is neither. Spelled out
      -- as a chain rather than one condition so the trace can name the reason a
      -- site was left out — which is the question asked of this log most often.
      local p = el and eyePoint(site.name) or nil
      if el == nil then
        trace(site.name .. " not in the net")
      elseif not (el.aiState == true or el:isDefendingHARM()) then
        trace(site.name .. " was cold — nothing was aimed at it")
      elseif not hasLiveRadar(site) then
        trace(site.name .. " has no radar left — destroyed, not suppressible")
      elseif p == nil then
        trace(site.name .. " has nothing alive to see with")
      elseif flat2(sp, p) > site.range then
        trace(string.format("%s is %s m from the launch, past its %s m reach",
          site.name, num(flat2(sp, p)), num(site.range)))
      else
        local los = seesFrom(p, sp)
        watching[#watching + 1] = {site = site, p = p, dist = flat2(sp, p), los = los}
        trace(string.format("%s is %s m from the launch and %s it",
          site.name, num(flat2(sp, p)), los and "sees" or "is masked from"))
        if los then observed = true end
      end
    end
    if #watching > 0 then
      trace(string.format("%s site(s) in reach, launch %s by the net",
        num(#watching), observed and "seen" or "seen by nobody"))
    end

    shotSeq = shotSeq + 1
    -- Only a launch somebody saw can be called down the net, so only that one
    -- leaves the net on notice.
    if observed then
      alert = {id = shotSeq, time = now, point = {x = sp.x, y = sp.y, z = sp.z}}
    end

    for _, w in ipairs(watching) do
      -- Evaluated now, so it must not be evaluated again on the way up.
      w.site.lastShot = shotSeq
      -- The roll is drawn either way, so reading the trace cannot change the
      -- outcome it is reporting.
      local roll = math.random()
      local chance = shotChance(w.site, w.los, observed)
      if roll <= chance then
        -- Drawn per site per shot, and deliberately of the same order as a
        -- HARM's time of flight: nobody gets a launch warning, so the shooter's
        -- range at launch is what decides whether the missile arrives before
        -- the transmitter dies.
        local site = w.site
        local delay, lo, hi = recognition(site, w.dist, w.los)
        trace(string.format(
          "%s acts on it (rolled %.2f against %.2f), radar off in %ss"
          .. " (recognition %s-%ss at this range%s)",
          site.name, roll, chance, num(delay), num(lo), num(hi),
          w.los and "" or ", relayed"))
        timer.scheduleFunction(function(_, when)
          suppress(site, when)
          return nil
        end, {}, now + delay)
      else
        trace(string.format("%s does not act on it (rolled %.2f against %.2f)",
          w.site.name, roll, chance))
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

  trace(string.format(
    "net up: %s site(s), %ss go-live cycle, %ss alert window, %s collector(s) "
    .. "able to report a radar changing state",
    num(#sites), num(updateInterval), num(alertWindow), num(#listeners)))

  -- Kept reachable for a mission that wants to reach into the net directly (a
  -- jammer, a command centre, a trigger that cuts a connection node). Global on
  -- purpose: separate DoScript actions do not share locals.
  dcsmcIADS = iads
end
