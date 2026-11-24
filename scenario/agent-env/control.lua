-- agent-env scenario
-- Goal: craft 50 iron plates. Not freeplay -- fixed spawn near a guaranteed
-- iron-ore patch, small burner-era starter kit, no research needed.
--
-- The agent drives a script-owned character stored in `storage.agent_character`,
-- so the scenario works on a headless server with no players connected. If a
-- graphical client joins, that player is attached to the same character so
-- they can watch (or take over).

local GOAL_IRON_PLATES = 50
local SEARCH_RADIUS_STEP = 32
local MAX_SEARCH_RADIUS = 512
local STATE_RADIUS = 32

local STATUS_NAMES = {}
for name, value in pairs(defines.entity_status) do
  STATUS_NAMES[value] = name
end

local function json(t)
  return helpers.table_to_json(t)
end

local function find_iron_ore_near_origin(surface)
  local radius = SEARCH_RADIUS_STEP
  while radius <= MAX_SEARCH_RADIUS do
    local ores = surface.find_entities_filtered{
      name = "iron-ore",
      position = { 0, 0 },
      radius = radius,
    }
    if #ores > 0 then
      return ores[1].position
    end
    radius = radius + SEARCH_RADIUS_STEP
  end
  return { x = 0, y = 0 }
end

local function give_starter_kit(character)
  character.insert{ name = "burner-mining-drill", count = 1 }
  character.insert{ name = "stone-furnace", count = 1 }
  character.insert{ name = "coal", count = 20 }
end

local function get_agent_character()
  local character = storage.agent_character
  if character and character.valid then
    return character
  end

  local surface = game.surfaces[1]
  local ore_position = find_iron_ore_near_origin(surface)
  local spawn = surface.find_non_colliding_position("character", ore_position, 16, 1) or ore_position
  character = surface.create_entity{
    name = "character",
    position = spawn,
    force = game.forces.player,
  }
  give_starter_kit(character)
  storage.agent_character = character
  return character
end

local function attach_player(player)
  local character = get_agent_character()
  if player.character == character then
    return
  end
  local old = player.character
  player.set_controller{ type = defines.controllers.character, character = character }
  if old and old.valid and old ~= character then
    old.destroy()
  end
end

local function inventory_contents_list(inventory)
  if not inventory then
    return {}
  end
  local list = {}
  for _, item in pairs(inventory.get_contents()) do
    table.insert(list, { name = item.name, count = item.count })
  end
  return list
end

local function entity_extra(entity)
  local t = entity.type
  if t == "resource" then
    return { amount = entity.amount }
  end
  if t == "furnace" then
    return {
      input = inventory_contents_list(entity.get_inventory(defines.inventory.crafter_input)),
      fuel = inventory_contents_list(entity.get_fuel_inventory()),
      output = inventory_contents_list(entity.get_output_inventory()),
      status = STATUS_NAMES[entity.status],
    }
  end
  if t == "mining-drill" then
    return {
      fuel = inventory_contents_list(entity.get_fuel_inventory()),
      status = STATUS_NAMES[entity.status],
      direction = entity.direction,
      drop_position = { x = entity.drop_position.x, y = entity.drop_position.y },
    }
  end
  return nil
end

local function build_state()
  local character = get_agent_character()
  local surface = character.surface
  local pos = character.position

  local inventory = {}
  local main_inv = character.get_main_inventory()
  if main_inv then
    for _, item in pairs(main_inv.get_contents()) do
      inventory[item.name] = item.count
    end
  end

  local nearby = {}
  for _, entity in pairs(surface.find_entities_filtered{ position = pos, radius = STATE_RADIUS }) do
    if entity.valid and entity ~= character then
      table.insert(nearby, {
        name = entity.name,
        type = entity.type,
        position = { x = entity.position.x, y = entity.position.y },
        extra = entity_extra(entity),
      })
    end
  end

  local crafting_queue = {}
  if character.crafting_queue then
    for _, item in pairs(character.crafting_queue) do
      table.insert(crafting_queue, { recipe = item.recipe, count = item.count })
    end
  end

  local iron_plates = character.get_item_count("iron-plate")

  return {
    tick = game.tick,
    position = { x = pos.x, y = pos.y },
    inventory = inventory,
    crafting_queue = crafting_queue,
    iron_plates = iron_plates,
    goal = GOAL_IRON_PLATES,
    goal_reached = iron_plates >= GOAL_IRON_PLATES,
    nearby_entities = nearby,
  }
end

local function find_entity_near(surface, name, x, y)
  local candidates = surface.find_entities_filtered{
    name = name,
    position = { x, y },
    radius = 2,
  }
  -- Prefer the closest match so adjacent entities of the same name don't get confused.
  local best, best_d
  for _, e in pairs(candidates) do
    local d = (e.position.x - x) ^ 2 + (e.position.y - y) ^ 2
    if not best or d < best_d then
      best, best_d = e, d
    end
  end
  return best
end

local actions = {}

function actions.move(character, action)
  local surface = character.surface
  local target = surface.find_non_colliding_position("character", { action.x, action.y }, 8, 0.5)
  if not target then
    return { ok = false, error = "no free position near target" }
  end
  character.teleport(target, surface)
  return { ok = true, position = target }
end

function actions.mine(character, action)
  local entity = find_entity_near(character.surface, action.name, action.x, action.y)
  if not entity then
    return { ok = false, error = "entity not found" }
  end
  local count = math.max(1, math.min(action.count or 1, 50))
  local before = character.get_item_count(action.name)
  -- mine_entity's return value is unreliable for resource entities (it
  -- returns false even when it yields an item), so measure success by the
  -- inventory delta instead. Resources yield an item named after the entity;
  -- for anything else (placed buildings, trees) we just count mine calls.
  local is_resource = entity.type == "resource"
  local mined = 0
  for _ = 1, count do
    if not entity.valid then
      break
    end
    local prev = character.get_item_count(action.name)
    local ok = character.mine_entity(entity, true)
    if is_resource then
      if character.get_item_count(action.name) <= prev then
        break
      end
    elseif not ok then
      break
    end
    mined = mined + 1
  end
  return { ok = mined > 0, mined = mined, inventory_delta = character.get_item_count(action.name) - before }
end

function actions.craft(character, action)
  local recipe = character.force.recipes[action.recipe or ""]
  if not recipe then
    return { ok = false, error = "unknown recipe: " .. tostring(action.recipe) }
  end
  local craftable = character.get_craftable_count(recipe)
  if craftable < 1 then
    return { ok = false, error = "missing ingredients", craftable = 0 }
  end
  local queued = character.begin_crafting{ recipe = recipe, count = math.min(action.count or 1, craftable) }
  return { ok = queued > 0, queued = queued }
end

function actions.wait(character, action)
  return { ok = true }
end

function actions.transfer_to_entity(character, action)
  local entity = find_entity_near(character.surface, action.name, action.x, action.y)
  if not entity then
    return { ok = false, error = "entity not found" }
  end
  local have = character.get_item_count(action.item)
  local count = math.min(action.count or have, have)
  if count < 1 then
    return { ok = false, error = "no " .. tostring(action.item) .. " in inventory" }
  end
  local inserted = entity.insert{ name = action.item, count = count }
  if inserted > 0 then
    character.remove_item{ name = action.item, count = inserted }
  end
  return { ok = inserted > 0, inserted = inserted, error = inserted == 0 and "entity cannot accept item" or nil }
end

function actions.collect_from_entity(character, action)
  local entity = find_entity_near(character.surface, action.name, action.x, action.y)
  if not entity then
    return { ok = false, error = "entity not found" }
  end
  local output_inv = entity.get_output_inventory()
  local collected = {}
  if output_inv then
    for _, item in pairs(output_inv.get_contents()) do
      local inserted = character.insert{ name = item.name, count = item.count, quality = item.quality }
      if inserted > 0 then
        output_inv.remove{ name = item.name, count = inserted, quality = item.quality }
        collected[item.name] = (collected[item.name] or 0) + inserted
      end
    end
  end
  return { ok = next(collected) ~= nil, collected = collected }
end

function actions.place_entity(character, action)
  local surface = character.surface
  if character.get_item_count(action.name) < 1 then
    return { ok = false, error = "no " .. tostring(action.name) .. " in inventory" }
  end
  local direction = action.direction or defines.direction.north
  local position = { action.x, action.y }
  local spec = { name = action.name, position = position, direction = direction, force = character.force }
  if not surface.can_place_entity(spec) then
    -- Fall back to the nearest free spot within a few tiles, unless the caller
    -- needs the exact position (e.g. a drill that must sit on ore).
    local alt = (not action.exact) and surface.find_non_colliding_position(action.name, position, 5, 1) or nil
    if not alt then
      return { ok = false, error = "cannot place there (blocked)" }
    end
    position = alt
  end
  local entity = surface.create_entity{
    name = action.name,
    position = position,
    direction = direction,
    force = character.force,
    raise_built = true,
  }
  if not entity then
    return { ok = false, error = "create_entity failed" }
  end
  character.remove_item{ name = action.name, count = 1 }
  return { ok = true, position = { x = entity.position.x, y = entity.position.y } }
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
  local character = get_agent_character()
  local ok, result = pcall(handler, character, action)
  if not ok then
    return json{ ok = false, error = "lua error: " .. tostring(result) }
  end
  return json(result)
end

remote.add_interface("agent", {
  get_state = function()
    return json(build_state())
  end,
  execute_action = function(json_str)
    return execute_action(json_str)
  end,
})

script.on_init(function()
  get_agent_character()
end)

script.on_event(defines.events.on_player_created, function(event)
  attach_player(game.get_player(event.player_index))
end)

script.on_event(defines.events.on_player_joined_game, function(event)
  attach_player(game.get_player(event.player_index))
end)
