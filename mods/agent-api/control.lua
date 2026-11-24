-- agent-api: lets an external agent observe and control player 1 over RCON
-- in any scenario (the built-in scenarios can't be edited, but a mod's script
-- runs alongside them).
--
-- Turn-based clock: the game is paused (game.tick_paused) and only advances
-- when an action costs game time. Each action computes a realistic cost
-- (walking distance / running speed, hand-mining time, explicit waits) and
-- queues it with game.ticks_to_run. The caller polls `pending_ticks` until
-- it reaches 0 before reading state again. Hand-crafting, furnaces, machines
-- and the scenario's own timers all progress only during those ticks.

local STATE_RADIUS = 24
local MAX_FIND_RADIUS = 300
local MAX_WAIT_SECONDS = 600

local STATUS_NAMES = {}
for name, value in pairs(defines.entity_status) do
  STATUS_NAMES[value] = name
end

local DIRECTIONS = {
  north = defines.direction.north,
  east = defines.direction.east,
  south = defines.direction.south,
  west = defines.direction.west,
}
local DIRECTION_NAMES = {}
for name, value in pairs(DIRECTIONS) do
  DIRECTION_NAMES[value] = name
end

-- Types that are scenery/terrain rather than buildings.
local SCENERY_TYPES = { resource = true, tree = true, ["simple-entity"] = true, fish = true, cliff = true,
  ["item-entity"] = true, character = true, corpse = true, ["character-corpse"] = true, decorative = true,
  ["optimized-decorative"] = true, ["highlight-box"] = true, explosion = true, ["particle-source"] = true,
  smoke = true }

local function json(t)
  return helpers.table_to_json(t)
end

local function round(v, places)
  local m = 10 ^ (places or 1)
  return math.floor(v * m + 0.5) / m
end

local function dist(a, b)
  return math.sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2)
end

local function get_player()
  local player = game.get_player(1)
  if not (player and player.connected and player.character and player.character.valid) then
    return nil, "no connected player with a character (is the graphical client connected?)"
  end
  return player, nil
end

local function advance(ticks)
  ticks = math.max(0, math.ceil(ticks))
  if ticks > 0 then
    game.ticks_to_run = (game.ticks_to_run or 0) + ticks
  end
  return ticks
end

local function contents(inventory)
  if not (inventory and inventory.valid) then
    return nil
  end
  local out = {}
  for _, item in pairs(inventory.get_contents()) do
    out[item.name] = (out[item.name] or 0) + item.count
  end
  if next(out) == nil then
    return nil
  end
  return out
end

-- Inventory ids overlap numerically (fuel == chest == turret_ammo == 1), so
-- pick the meaningful inventories per entity type instead of probing them all.
local INVENTORIES_BY_TYPE = {
  furnace = { { "fuel", defines.inventory.fuel }, { "input", defines.inventory.crafter_input },
    { "output", defines.inventory.crafter_output } },
  ["assembling-machine"] = { { "input", defines.inventory.crafter_input },
    { "output", defines.inventory.crafter_output } },
  ["mining-drill"] = { { "fuel", defines.inventory.fuel } },
  boiler = { { "fuel", defines.inventory.fuel } },
  container = { { "chest", defines.inventory.chest } },
  ["logistic-container"] = { { "chest", defines.inventory.chest } },
  lab = { { "science_packs", defines.inventory.lab_input } },
  ["ammo-turret"] = { { "ammo", defines.inventory.turret_ammo } },
  car = { { "fuel", defines.inventory.fuel } },
  inserter = {},
}

local function entity_info(entity, origin)
  local info = {
    name = entity.name,
    position = { x = entity.position.x, y = entity.position.y },
    dist = round(dist(origin, entity.position)),
  }
  if entity.supports_direction then
    info.direction = DIRECTION_NAMES[entity.direction] or entity.direction
  end
  local ok_status, status = pcall(function() return entity.status end)
  if ok_status and status then
    info.status = STATUS_NAMES[status]
  end
  if entity.type == "assembling-machine" or entity.type == "furnace" then
    local recipe = entity.get_recipe()
    if recipe then
      info.recipe = recipe.name
    end
  end
  for _, slot in ipairs(INVENTORIES_BY_TYPE[entity.type] or {}) do
    local ok, inv = pcall(entity.get_inventory, slot[2])
    if ok and inv then
      local c = contents(inv)
      if c then
        info[slot[1]] = c
      end
    end
  end
  if entity.type == "mining-drill" then
    info.drop_position = { x = round(entity.drop_position.x), y = round(entity.drop_position.y) }
  end
  return info
end

local function build_state(player)
  local character = player.character
  local surface = character.surface
  local pos = character.position

  local buildings, resources = {}, {}
  for _, e in pairs(surface.find_entities_filtered{ position = pos, radius = STATE_RADIUS }) do
    if e.valid and e ~= character then
      local t = e.type
      if t == "resource" or t == "tree" or (t == "simple-entity" and e.prototype.mineable_properties.minable) then
        local key = (t == "tree") and "tree" or e.name
        local bucket = resources[key]
        if not bucket then
          bucket = { count = 0, list = {} }
          resources[key] = bucket
        end
        bucket.count = bucket.count + 1
        table.insert(bucket.list, { x = e.position.x, y = e.position.y, d = dist(pos, e.position), name = e.name })
      elseif not SCENERY_TYPES[t] then
        -- Any structure, including neutral scenario-placed ones (e.g. delivery chests).
        table.insert(buildings, entity_info(e, pos))
      end
    end
  end
  table.sort(buildings, function(a, b) return a.dist < b.dist end)

  local resource_summary = {}
  for key, bucket in pairs(resources) do
    table.sort(bucket.list, function(a, b) return a.d < b.d end)
    local nearest = {}
    for i = 1, math.min(3, #bucket.list) do
      local r = bucket.list[i]
      local entry = { x = r.x, y = r.y, dist = round(r.d) }
      if key == "tree" then
        entry.name = r.name
      end
      table.insert(nearest, entry)
    end
    resource_summary[key] = { tiles_in_view = bucket.count, nearest = nearest }
  end

  local crafting_queue = {}
  if character.crafting_queue then
    for _, item in pairs(character.crafting_queue) do
      table.insert(crafting_queue, { recipe = item.recipe, count = item.count })
    end
  end

  local force = character.force
  local research = nil
  if force.current_research then
    research = { name = force.current_research.name, progress = round(force.research_progress, 2) }
  end

  return {
    tick = game.tick,
    pending_ticks = game.ticks_to_run or 0,
    position = { x = round(pos.x, 2), y = round(pos.y, 2) },
    inventory = contents(character.get_main_inventory()) or {},
    crafting_queue = crafting_queue,
    research = research,
    buildings = buildings,
    resources = resource_summary,
  }
end

local function find_entity_near(surface, name, x, y)
  local best, best_d
  for _, e in pairs(surface.find_entities_filtered{ name = name, position = { x, y }, radius = 2 }) do
    local d = dist(e.position, { x = x, y = y })
    if not best or d < best_d then
      best, best_d = e, d
    end
  end
  return best
end

local function require_entity(character, action, reach_check)
  local entity = find_entity_near(character.surface, action.name or "", action.x or 0, action.y or 0)
  if not entity then
    return nil, "no " .. tostring(action.name) .. " within 2 tiles of (" .. tostring(action.x) .. ", " .. tostring(action.y) .. ")"
  end
  if reach_check ~= false and not character.can_reach_entity(entity) then
    return nil, "out of reach (distance " .. round(dist(character.position, entity.position)) ..
      ", reach " .. character.reach_distance .. "); move closer first"
  end
  return entity, nil
end

local function mining_speed(character)
  return character.prototype.mining_speed * (1 + character.force.manual_mining_speed_modifier + character.character_mining_speed_modifier)
end

local actions = {}

function actions.move(player, action)
  local character = player.character
  local surface = character.surface
  local target = surface.find_non_colliding_position("character", { action.x, action.y }, 10, 0.5)
  if not target then
    return { ok = false, error = "no free position near target" }
  end
  local distance = dist(character.position, target)
  local speed = character.character_running_speed -- tiles per tick
  local ticks = advance(distance / speed)
  character.teleport(target, surface)
  return { ok = true, position = target, distance = round(distance), game_seconds = round(ticks / 60) }
end

function actions.mine(player, action)
  local character = player.character
  local entity, err = require_entity(character, action, false)
  if not entity then
    return { ok = false, error = err }
  end
  local is_resource = entity.type == "resource"
  local reach = is_resource and (character.resource_reach_distance + 0.75) or character.reach_distance
  local d = dist(character.position, entity.position)
  if d > reach then
    return { ok = false, error = "out of mining reach (distance " .. round(d) .. ", reach " .. round(reach) .. "); move closer first" }
  end
  if not entity.minable then
    return { ok = false, error = entity.name .. " is not minable" }
  end
  local per_unit_ticks = entity.prototype.mineable_properties.mining_time / mining_speed(character) * 60
  local count = is_resource and math.max(1, math.min(action.count or 1, 100)) or 1
  local before = contents(character.get_main_inventory()) or {}
  local mined = 0
  for _ = 1, count do
    if not entity.valid then
      break
    end
    local inv_before = character.get_main_inventory().get_item_count()
    local ok = character.mine_entity(entity, true)
    local gained = character.get_main_inventory().get_item_count() > inv_before
    if not (ok or gained) then
      break
    end
    mined = mined + 1
  end
  local ticks = advance(per_unit_ticks * mined)
  local after = contents(character.get_main_inventory()) or {}
  local gained = {}
  for name, c in pairs(after) do
    if c > (before[name] or 0) then
      gained[name] = c - (before[name] or 0)
    end
  end
  return { ok = mined > 0, mined = mined, gained = gained, game_seconds = round(ticks / 60),
    error = mined == 0 and "nothing mined (inventory full?)" or nil }
end

function actions.place(player, action)
  local character = player.character
  local surface = character.surface
  local name = action.name or ""
  if character.get_item_count(name) < 1 then
    return { ok = false, error = "no " .. name .. " in inventory" }
  end
  local item = prototypes.item[name]
  local place_result = item and item.place_result
  if not place_result then
    return { ok = false, error = name .. " is not placeable" }
  end
  local position = { action.x, action.y }
  if dist(character.position, { x = action.x, y = action.y }) > character.build_distance then
    return { ok = false, error = "too far to build (build distance " .. character.build_distance .. "); move closer first" }
  end
  local direction = DIRECTIONS[action.direction or "north"] or defines.direction.north
  local spec = { name = place_result.name, position = position, direction = direction, force = character.force,
    build_check_type = defines.build_check_type.manual }
  if not surface.can_place_entity(spec) then
    return { ok = false, error = "cannot place " .. name .. " at (" .. action.x .. ", " .. action.y .. "): blocked or invalid" }
  end
  local entity = surface.create_entity{ name = place_result.name, position = position, direction = direction,
    force = character.force, player = player, raise_built = true }
  if not entity then
    return { ok = false, error = "create_entity failed" }
  end
  character.remove_item{ name = name, count = 1 }
  return { ok = true, entity = entity.name, position = { x = entity.position.x, y = entity.position.y },
    direction = DIRECTION_NAMES[entity.direction] }
end

function actions.rotate(player, action)
  local entity, err = require_entity(player.character, action)
  if not entity then
    return { ok = false, error = err }
  end
  local direction = DIRECTIONS[action.direction or ""]
  if not direction then
    return { ok = false, error = "direction must be north/east/south/west" }
  end
  if not entity.supports_direction then
    return { ok = false, error = entity.name .. " cannot be rotated" }
  end
  entity.direction = direction
  return { ok = true, direction = DIRECTION_NAMES[entity.direction] }
end

function actions.insert(player, action)
  local character = player.character
  local entity, err = require_entity(character, action)
  if not entity then
    return { ok = false, error = err }
  end
  local have = character.get_item_count(action.item or "")
  local count = math.min(action.count or have, have)
  if count < 1 then
    return { ok = false, error = "no " .. tostring(action.item) .. " in inventory" }
  end
  local inserted = entity.insert{ name = action.item, count = count }
  if inserted > 0 then
    character.remove_item{ name = action.item, count = inserted }
  end
  return { ok = inserted > 0, inserted = inserted, error = inserted == 0 and (entity.name .. " cannot accept " .. action.item) or nil }
end

function actions.take(player, action)
  local character = player.character
  local entity, err = require_entity(character, action)
  if not entity then
    return { ok = false, error = err }
  end
  local taken = {}
  local remaining = action.count or math.huge
  for _, slot in ipairs(INVENTORIES_BY_TYPE[entity.type] or { { "chest", defines.inventory.chest } }) do
    local role, id = slot[1], slot[2]
    -- Without an explicit item, only empty output/chest inventories (never fuel or ingredients).
    local eligible = action.item ~= nil or role == "output" or role == "chest"
    local ok, inv = pcall(entity.get_inventory, id)
    if eligible and ok and inv and inv.valid then
      for _, item in pairs(inv.get_contents()) do
        if remaining <= 0 then break end
        if action.item == nil or item.name == action.item then
          local moved = character.insert{ name = item.name, count = math.min(item.count, remaining), quality = item.quality }
          if moved > 0 then
            inv.remove{ name = item.name, count = moved, quality = item.quality }
            taken[item.name] = (taken[item.name] or 0) + moved
            remaining = remaining - moved
          end
        end
      end
    end
  end
  return { ok = next(taken) ~= nil, taken = taken, error = next(taken) == nil and "nothing to take" or nil }
end

function actions.craft(player, action)
  local character = player.character
  local recipe = character.force.recipes[action.recipe or ""]
  if not recipe then
    return { ok = false, error = "unknown recipe: " .. tostring(action.recipe) }
  end
  if not recipe.enabled then
    return { ok = false, error = "recipe " .. recipe.name .. " is not unlocked (needs research)" }
  end
  local craftable = character.get_craftable_count(recipe)
  if craftable < 1 then
    return { ok = false, error = "missing ingredients (or recipe cannot be hand-crafted)", craftable = 0 }
  end
  local queued = character.begin_crafting{ recipe = recipe, count = math.min(action.count or 1, craftable) }
  return { ok = queued > 0, queued = queued,
    note = "crafting happens over game time; use wait (or other timed actions) to let it finish" }
end

function actions.set_recipe(player, action)
  local entity, err = require_entity(player.character, action)
  if not entity then
    return { ok = false, error = err }
  end
  if entity.type ~= "assembling-machine" then
    return { ok = false, error = entity.name .. " has no settable recipe" }
  end
  local ok, res = pcall(entity.set_recipe, action.recipe)
  if not ok then
    return { ok = false, error = tostring(res) }
  end
  return { ok = true, recipe = action.recipe }
end

function actions.research(player, action)
  local force = player.force
  local tech = force.technologies[action.technology or ""]
  if not tech then
    return { ok = false, error = "unknown technology: " .. tostring(action.technology) }
  end
  if tech.researched then
    return { ok = false, error = tech.name .. " is already researched" }
  end
  local added = force.add_research(tech)
  return { ok = added, error = (not added) and "could not queue (missing prerequisites?)" or nil }
end

function actions.wait(player, action)
  local seconds = math.max(0.1, math.min(action.seconds or 1, MAX_WAIT_SECONDS))
  local ticks = advance(seconds * 60)
  return { ok = true, game_seconds = round(ticks / 60) }
end

-- Read-only helpers (no time cost).

function actions.find_nearest(player, action)
  local character = player.character
  local radius = math.min(action.radius or 100, MAX_FIND_RADIUS)
  local pos = character.position
  local filter = { position = pos, radius = radius }
  if action.name == "tree" then
    filter.type = "tree"
  else
    filter.name = action.name
  end
  local ok, found = pcall(character.surface.find_entities_filtered, filter)
  if not ok then
    return { ok = false, error = "unknown entity name: " .. tostring(action.name) }
  end
  table.sort(found, function(a, b) return dist(pos, a.position) < dist(pos, b.position) end)
  local out = {}
  for i = 1, math.min(action.limit or 5, #found, 20) do
    local e = found[i]
    local entry = { name = e.name, x = e.position.x, y = e.position.y, dist = round(dist(pos, e.position)) }
    if e.type == "resource" then
      entry.amount = e.amount
    end
    table.insert(out, entry)
  end
  return { ok = true, total_within_radius = #found, nearest = out }
end

function actions.recipe_info(player, action)
  local recipe = player.force.recipes[action.recipe or ""]
  if not recipe then
    return { ok = false, error = "unknown recipe: " .. tostring(action.recipe) }
  end
  local function list(items)
    local out = {}
    for _, i in pairs(items) do
      table.insert(out, { name = i.name, amount = i.amount or i.amount_min, type = i.type })
    end
    return out
  end
  return { ok = true, name = recipe.name, enabled = recipe.enabled,
    crafting_seconds = recipe.energy, ingredients = list(recipe.ingredients), products = list(recipe.products) }
end

function actions.technology_info(player, action)
  local force = player.force
  if action.technology then
    local tech = force.technologies[action.technology]
    if not tech then
      return { ok = false, error = "unknown technology" }
    end
    local prereqs, unlocks, cost = {}, {}, {}
    for name in pairs(tech.prerequisites) do table.insert(prereqs, name) end
    for _, effect in pairs(tech.prototype.effects) do
      if effect.type == "unlock-recipe" then table.insert(unlocks, effect.recipe) end
    end
    for _, ing in pairs(tech.research_unit_ingredients) do table.insert(cost, { name = ing.name, amount = ing.amount }) end
    local trigger = tech.prototype.research_trigger
    if trigger then
      -- Trigger technologies unlock by doing something (craft/mine/build an item), not via labs.
      return { ok = true, name = tech.name, researched = tech.researched, prerequisites = prereqs,
        unlocks_recipes = unlocks, unlocked_by_trigger = trigger }
    end
    return { ok = true, name = tech.name, researched = tech.researched, prerequisites = prereqs, unlocks_recipes = unlocks,
      unit_count = tech.research_unit_count, unit_ingredients = cost, unit_seconds = tech.research_unit_energy / 60 }
  end
  local available = {}
  for name, tech in pairs(force.technologies) do
    if tech.enabled and not tech.researched then
      local ready = true
      for _, pre in pairs(tech.prerequisites) do
        if not pre.researched then ready = false break end
      end
      if ready then table.insert(available, name) end
    end
  end
  table.sort(available)
  return { ok = true, researchable_now = available }
end

local function execute_action(json_str)
  local action = helpers.json_to_table(json_str)
  if not action or not action.type then
    return json{ ok = false, error = "invalid action" }
  end
  local handler = actions[action.type]
  if not handler then
    return json{ ok = false, error = "unknown action type: " .. tostring(action.type) }
  end
  local player, err = get_player()
  if not player then
    return json{ ok = false, error = err }
  end
  local ok, result = pcall(handler, player, action)
  if not ok then
    return json{ ok = false, error = "lua error: " .. tostring(result) }
  end
  return json(result)
end

remote.add_interface("agent_api", {
  get_state = function()
    local player, err = get_player()
    if not player then
      return json{ error = err, tick = game.tick, pending_ticks = game.ticks_to_run or 0 }
    end
    return json(build_state(player))
  end,
  execute_action = execute_action,
  pending_ticks = function()
    return game.ticks_to_run or 0
  end,
  set_paused = function(paused)
    game.tick_paused = paused
    return json{ ok = true, tick_paused = game.tick_paused }
  end,
})

script.on_init(function()
  -- Start paused so the scenario's level timers don't run before the agent is ready.
  game.tick_paused = true
end)
