-- dcs_mission_creator: JTAC target coordinates in the requesting cockpit's format.
--
-- Rendered by `core/jtac.py` into a mission-start DoScript. It fills six
-- placeholders (named here without their underscores so this comment is not
-- itself substituted): TARGETS is one `{group=…, label=…, …}` row per target,
-- FORMATS one `["<DCS type>"] = "<format key>"` row per airframe, DEFAULT the
-- format an unlisted airframe gets, MENU the radio sub-menu title, SIDE the
-- coalition whose players get the menu, DURATION how long the readout stays on
-- screen, SCAN the seconds between two sweeps for newly slotted players, PUSH AT
-- the mission time of the one unprompted readout (or nil for request-only).
do
  local targets = {
__TARGETS__
  }
  local formats = {
__FORMATS__
  }
  local default = __DEFAULT__
  local menuTitle = __MENU__
  local side = __SIDE__
  local duration = __DURATION__
  local scan = __SCAN__
  local pushAt = __PUSH_AT__

  -- Degrees + decimal minutes is the shape both lat/long writers start from:
  -- the deg-min-sec one just splits the minutes again. Rounding is carried
  -- upward, or 35.99999° prints as 35°60.000'.
  local function degMin(value)
    local positive = value >= 0
    local v = math.abs(value)
    local d = math.floor(v)
    local m = (v - d) * 60
    if m >= 59.9995 then
      d, m = d + 1, 0
    end
    return positive, d, m
  end

  local function ddm(lat, lon)
    local north, latD, latM = degMin(lat)
    local east, lonD, lonM = degMin(lon)
    return string.format("%s %02d %06.3f  %s %03d %06.3f",
      north and "N" or "S", latD, latM,
      east and "E" or "W", lonD, lonM)
  end

  local function dms(lat, lon)
    local function part(value, fmt, pos, neg)
      local positive, d, m = degMin(value)
      local s = (m - math.floor(m)) * 60
      m = math.floor(m)
      if s >= 59.95 then
        m, s = m + 1, 0
      end
      if m >= 60 then
        d, m = d + 1, 0
      end
      return string.format(fmt, positive and pos or neg, d, m, s)
    end
    return part(lat, "%s %02d %02d %04.1f", "N", "S") .. "  " ..
      part(lon, "%s %03d %02d %04.1f", "E", "W")
  end

  -- 4-digit easting/northing (10 m), zone and digraph included: that is the
  -- whole grid an A-10's CDU or an Apache's TSD wants typed in.
  local function mgrs(lat, lon)
    local grid = coord.LLtoMGRS(lat, lon)
    local function digits(v)
      local n = math.floor(v / 10 + 0.5)
      if n > 9999 then n = 9999 end
      return string.format("%04d", n)
    end
    return string.format("%s %s %s %s", grid.UTMZone, grid.MGRSDigraph,
      digits(grid.Easting), digits(grid.Northing))
  end

  local writers = {mgrs = mgrs, ddm = ddm, dms = dms}
  local labels = {mgrs = "MGRS", ddm = "lat/long deg-min", dms = "lat/long deg-min-sec"}

  -- Read the position off a live unit, not off the group's spawn point: the
  -- column moves, which is the reason this is a request and not a briefing line.
  local function targetPoint(target)
    local group = Group.getByName(target.group)
    if group == nil or not group:isExist() then return nil end
    local units = group:getUnits()
    if units == nil then return nil end
    for _, unit in ipairs(units) do
      if unit:isExist() and unit:getLife() > 0 then return unit:getPoint() end
    end
    return nil
  end

  local function report(args)
    local target, groupId, format = args.target, args.groupId, args.format
    local point = targetPoint(target)
    if point == nil then
      trigger.action.outTextForGroup(groupId,
        string.format("%s: nothing left of %s to pass.", target.label, target.what),
        duration)
      return
    end
    local lat, lon = coord.LOtoLL(point)
    local elevation = land.getHeight({x = point.x, y = point.z})
    local lines = {
      string.format("%s: %s.", target.label, target.what),
      string.format("Position (%s): %s", labels[format] or format,
        writers[format](lat, lon)),
      string.format("Elevation: %d ft (%d m)",
        math.floor(elevation * 3.28084 + 0.5), math.floor(elevation + 0.5)),
    }
    if target.code then
      lines[#lines + 1] = string.format("Laser code %d.", target.code)
    end
    trigger.action.outTextForGroup(groupId, table.concat(lines, "\n"), duration)
  end

  -- Volunteered readout: DCS's own 9-line is a grid whatever the airframe, so a
  -- player who never opens the F10 menu would only ever be read a grid. One
  -- unprompted position in the right format says the readout exists; after that
  -- it is on request, because the target moves and a stream of updates is not
  -- what a controller does.
  local function pushFirst(args)
    report({target = targets[1], groupId = args.groupId, format = args.format})
    return nil
  end

  -- The format is picked per *group*, when that group first shows up with a
  -- human in it: a player who swaps to another airframe swaps group with it and
  -- gets that cockpit's format on the new group's menu.
  local wired = {}

  local function wire(unit, time)
    if unit == nil or not unit:isExist() then return end
    local group = unit:getGroup()
    if group == nil or not group:isExist() then return end
    local groupId = group:getID()
    if wired[groupId] then return end
    wired[groupId] = true
    local format = formats[unit:getTypeName()] or default
    local root = missionCommands.addSubMenuForGroup(groupId, menuTitle)
    for _, target in ipairs(targets) do
      missionCommands.addCommandForGroup(groupId, target.item, root, report,
        {target = target, groupId = groupId, format = format})
    end
    if pushAt then
      -- Someone who slots in after the controller has already checked in still
      -- gets the call, a few seconds after they are in the cockpit.
      timer.scheduleFunction(pushFirst, {groupId = groupId, format = format},
        math.max(pushAt, time + 10))
    end
  end

  -- Polling rather than an S_EVENT_BIRTH handler: a client only exists once
  -- someone takes the slot, which in multiplayer happens minutes after the
  -- mission starts and again on every respawn or slot change.
  local function sweep(_, time)
    local players = coalition.getPlayers(side)
    if players then
      for _, unit in ipairs(players) do wire(unit, time) end
    end
    return time + scan
  end

  timer.scheduleFunction(sweep, {}, timer.getTime() + 1)
end
