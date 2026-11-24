-- agent-env scenario
-- Goal: craft 50 iron plates. Not freeplay -- fixed spawn near a guaranteed
-- iron-ore patch, small burner-era starter kit, no research needed.

local GOAL_IRON_PLATES = 50
local SEARCH_RADIUS_STEP = 32
local MAX_SEARCH_RADIUS = 512

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

local function setup_player(player)
  local surface = player.surface
  local ore_position = find_iron_ore_near_origin(surface)

  local spawn = surface.find_non_colliding_position("character", ore_position, 16, 1) or ore_position
  if player.character then
    player.teleport(spawn, surface)
  end

  player.insert{ name = "burner-mining-drill", count = 1 }
  player.insert{ name = "stone-furnace", count = 1 }
  player.insert{ name = "coal", count = 20 }
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
  if entity.type ~= "furnace" then
    return nil
  end
  return {
    input = inventory_contents_list(entity.get_inventory(defines.inventory.furnace_source)),
    fuel = inventory_contents_list(entity.get_fuel_inventory()),
    output = inventory_contents_list(entity.get_output_inventory()),
  }
end

local function build_state()
  local player = game.get_player(1)
  if not player or not player.character then
    return { error = "no player character" }
  end

  local character = player.character
  local surface = character.surface
  local pos = character.position

  local inventory = {}
  local main_inv = player.get_main_inventory()
  if main_inv then
    local contents = main_inv.get_contents()
    for _, item in pairs(contents) do
      inventory[item.name] = item.count
    end
  end

  local nearby = {}
  local nearby_entities = surface.find_entities_filtered{
    position = pos,
    radius = 32,
  }
  for _, entity in pairs(nearby_entities) do
    if entity.valid and entity ~= character then
      table.insert(nearby, {
        name = entity.name,
        position = { x = entity.position.x, y = entity.position.y },
        extra = entity_extra(entity),
      })
    end
  end

  local iron_plates = player.get_item_count("iron-plate")

  return {
    tick = game.tick,
    position = { x = pos.x, y = pos.y },
    inventory = inventory,
    iron_plates = iron_plates,
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
  return candidates[1]
end

local function execute_action(json_str)
  local action = helpers.json_to_table(json_str)
  if not action or not action.type then
    return helpers.table_to_json{ ok = false, error = "invalid action" }
  end

  local player = game.get_player(1)
  if not player or not player.character then
    return helpers.table_to_json{ ok = false, error = "no player character" }
  end
  local character = player.character
  local surface = character.surface

  if action.type == "move" then
    character.teleport({ action.x, action.y }, surface)
    return helpers.table_to_json{ ok = true }
  end

  if action.type == "mine" then
    local entity = find_entity_near(surface, action.name, action.x, action.y)
    if not entity then
      return helpers.table_to_json{ ok = false, error = "entity not found" }
    end
    local mined = character.mine_entity(entity, true)
    return helpers.table_to_json{ ok = mined }
  end

  if action.type == "craft" then
    local queued = player.begin_crafting{
      recipe = action.recipe,
      count = action.count or 1,
    }
    return helpers.table_to_json{ ok = queued ~= nil, queued = queued }
  end

  if action.type == "wait" then
    return helpers.table_to_json{ ok = true }
  end

  if action.type == "transfer_to_entity" then
    local entity = find_entity_near(surface, action.name, action.x, action.y)
    if not entity then
      return helpers.table_to_json{ ok = false, error = "entity not found" }
    end
    local removed = player.get_main_inventory().remove{ name = action.item, count = action.count }
    if removed > 0 then
      entity.insert{ name = action.item, count = removed }
    end
    return helpers.table_to_json{ ok = removed > 0, removed = removed }
  end

  if action.type == "collect_from_entity" then
    local entity = find_entity_near(surface, action.name, action.x, action.y)
    if not entity then
      return helpers.table_to_json{ ok = false, error = "entity not found" }
    end
    local output_inv = entity.get_output_inventory()
    local collected = {}
    if output_inv then
      for _, item in pairs(output_inv.get_contents()) do
        player.insert{ name = item.name, count = item.count }
        output_inv.remove{ name = item.name, count = item.count }
        collected[item.name] = item.count
      end
    end
    return helpers.table_to_json{ ok = true, collected = collected }
  end

  if action.type == "place_entity" then
    local entity = surface.create_entity{
      name = action.name,
      position = { action.x, action.y },
      direction = action.direction or defines.direction.north,
      force = character.force,
    }
    return helpers.table_to_json{ ok = entity ~= nil }
  end

  return helpers.table_to_json{ ok = false, error = "unknown action type: " .. tostring(action.type) }
end

remote.add_interface("agent", {
  get_state = function()
    return helpers.table_to_json(build_state())
  end,
  execute_action = function(json_str)
    return execute_action(json_str)
  end,
})

script.on_init(function()
  local player = game.get_player(1)
  if player then
    setup_player(player)
  end
end)

script.on_event(defines.events.on_player_created, function(event)
  local player = game.get_player(event.player_index)
  setup_player(player)
end)
