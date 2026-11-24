"""Claude plays the built-in Supply challenge scenario (base/supply).

Supply challenge: 21 timed levels; each requires delivering specific items
into the red chests near spawn. Finishing a level early (the "Next level"
button) scores a time bonus; running out of time with requirements unmet ends
the game. Score = levels completed + points.

The agent-api mod provides the character controls (with game-time costs, clock
paused between actions); scenario progress is read directly from the
scenario's own Lua state over /sc.
"""

from __future__ import annotations

import json
import time
from typing import Any

from factorio_agent.claude_loop import run_tool_loop, tool
from factorio_agent.rcon_client import RCONClient

GAME_SPEED = 30  # how fast queued ticks are processed; doesn't change game-time costs
MAX_BUILDINGS_IN_STATE = 15

_SCENARIO_STATE_LUA = r"""
local function reqs(level)
  local out = {}
  if not level then return out end
  for _, it in pairs(level.requirements) do
    table.insert(out, {name = it.name, required = it.count,
      delivered = (storage.accumulated and storage.accumulated[it.name]) or 0})
  end
  return out
end
local level = levels[storage.level]
local next_level = levels[storage.level + 1]
local nxt = {}
if next_level then
  for _, it in pairs(next_level.requirements) do table.insert(nxt, {name = it.name, count = it.count}) end
end
local chests = {}
for _, c in pairs(storage.chests or {}) do
  if c.valid then table.insert(chests, {x = c.position.x, y = c.position.y}) end
end
local met = level ~= nil and storage.accumulated ~= nil
if met then
  for _, it in pairs(level.requirements) do
    if (storage.accumulated[it.name] or 0) < it.count then met = false end
  end
end
rcon.print(helpers.table_to_json{
  level = storage.level, levels_total = #levels, points = math.floor(storage.points),
  time_left_seconds = storage.level_started_at and math.floor(get_time_left() / 60) or nil,
  level_time_limit_seconds = level and math.floor(level.time * time_modifier) or nil,
  requirements = reqs(level), requirements_met = met, next_level_requirements = nxt,
  delivery_chests = chests, game_finished = game.finished, tick = game.tick})
"""

# Same effect as a player clicking the scenario's "Next level" button: the
# scenario's own story handler awards the time bonus and advances the level.
_COMPLETE_LEVEL_LUA = r"""
story_update(storage.story, {name = defines.events.on_gui_click,
  element = {name = "next_level", valid = true}, player_index = 1, tick = game.tick}, "")
"""


def _lua_str(text: str) -> str:
    return json.dumps(text)  # JSON string literal is a valid Lua string literal for our content


def _tidy(value: Any) -> Any:
    """Round floats (Lua's JSON encoder prints full doubles) to keep payloads small."""
    if isinstance(value, float):
        return round(value, 1)
    if isinstance(value, dict):
        return {k: _tidy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_tidy(v) for v in value]
    return value


_INVENTORY_ROLES = ("fuel", "input", "output", "chest", "science_packs", "ammo")


def _machine_line(b: dict[str, Any]) -> str:
    """e.g. 'stone-furnace@82,56 working fuel:coal=2 out:iron-plate=10'."""
    x, y = b["position"]["x"], b["position"]["y"]
    parts = [f"{b['name']}@{x:g},{y:g}"]
    if b.get("direction"):
        parts.append(b["direction"])
    if b.get("status"):
        parts.append(b["status"])
    if b.get("recipe"):
        parts.append(f"recipe:{b['recipe']}")
    for role in _INVENTORY_ROLES:
        if b.get(role):
            label = "out" if role == "output" else role
            parts.append(label + ":" + ",".join(f"{k}={v}" for k, v in b[role].items()))
    return " ".join(parts)


class SupplyGame:
    def __init__(self, rcon: RCONClient):
        self.rcon = rcon

    def _sc(self, lua: str) -> str:
        return self.rcon.send_command("/sc " + " ".join(lua.split()))

    def _json(self, lua: str) -> dict[str, Any]:
        out = self._sc(lua)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise RuntimeError(f"bad response from game: {out[:300]}") from None

    def start(self) -> None:
        """Pause the clock and (on a fresh game) restart level 1's timer from now.

        The scenario starts its level-1 timer at tick ~2, before any agent can
        connect, so for a fair run the timer starts when the agent does.
        """
        self._sc('remote.call("agent_api", "set_paused", true)')
        self._sc(f"game.speed = {GAME_SPEED}")
        self._sc("if storage.level == 1 and storage.points == 0 then storage.level_started_at = game.tick end")

    def stop(self) -> None:
        self._sc("game.speed = 1")

    def wait_for_ticks(self, timeout: float = 120.0) -> None:
        deadline = time.monotonic() + timeout
        while int(self._sc('rcon.print(remote.call("agent_api", "pending_ticks"))') or 0) > 0:
            if time.monotonic() > deadline:
                raise TimeoutError("game did not finish queued ticks")
            time.sleep(0.05)

    def scenario(self) -> dict[str, Any]:
        return _tidy(self._json(_SCENARIO_STATE_LUA))

    def character(self) -> dict[str, Any]:
        state = _tidy(self._json('rcon.print(remote.call("agent_api", "get_state"))'))
        if "buildings" in state:
            # Delivery chests are listed separately in the scenario block.
            state["buildings"] = [b for b in state["buildings"] if b["name"] != "red-chest"][:MAX_BUILDINGS_IN_STATE]
        state.pop("pending_ticks", None)
        if not state.get("crafting_queue"):
            state["crafting_queue"] = []  # Lua encodes an empty table as {}
        return state

    def state(self) -> dict[str, Any]:
        return {"scenario": self.scenario(), "character": self.character()}

    def status(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Compact per-action status (returned after every timed action), plus the raw scenario block.

        Full detail (nearby resources, delivery chest positions, next level's
        requirements, drill drop positions) is only in get_state, to keep the
        conversation, and therefore every later request, small.
        """
        sc, ch = self.scenario(), self.character()
        out: dict[str, Any] = {
            "level": sc["level"],
            "time_left_s": sc.get("time_left_seconds"),
            "need": [f"{r['name']} {r['delivered']}/{r['required']}" for r in sc["requirements"]],
            "met": sc["requirements_met"],
            "pos": [ch.get("position", {}).get("x"), ch.get("position", {}).get("y")],
            "inv": ch.get("inventory", {}),
        }
        if ch.get("crafting_queue"):
            out["crafting"] = [f"{q['recipe']} x{q['count']}" for q in ch["crafting_queue"]]
        if ch.get("research"):
            out["research"] = ch["research"]
        machines = [_machine_line(b) for b in ch.get("buildings", [])]
        if machines:
            out["machines"] = machines
        return out, sc

    def act(self, action: dict[str, Any]) -> dict[str, Any]:
        if action["type"] == "complete_level":
            return self.complete_level()
        result = self._json(f'rcon.print(remote.call("agent_api", "execute_action", {_lua_str(json.dumps(action))}))')
        self.wait_for_ticks()
        return _tidy(result)

    def complete_level(self) -> dict[str, Any]:
        before = self.scenario()
        if not before["requirements_met"]:
            return {"ok": False, "error": "requirements not met yet (delivered items are counted once per game second)",
                    "requirements": before["requirements"]}
        self._sc(_COMPLETE_LEVEL_LUA)
        self._sc("game.ticks_to_run = 2")  # let the scenario run its level-start step
        self.wait_for_ticks()
        after = self.scenario()
        return {"ok": after["level"] > before["level"], "completed_level": before["level"],
                "points_gained": after["points"] - before["points"], "now_level": after["level"]}


SYSTEM_PROMPT = """You are playing Factorio (2.x, base game) through a tool API, in the built-in \
"Supply challenge" scenario.

Scenario rules:
- There are 21 levels. Each level asks you to deliver specific items (e.g. 15 stone, then 30 iron \
plates, ...) into the red delivery chests near spawn, before that level's timer runs out.
- Items in the delivery chests are counted once per game second. When the requirements are met, call \
`complete_level` immediately: unused time converts into bonus points. Finishing a level also gives points.
- If a level's timer runs out with requirements unmet, the game is over. Goal: complete as many levels as \
possible, with as many points as possible.
- The state shows the current and next level's requirements, so you can prepare ahead.

How time works in this API:
- The game clock is paused while you think. It only advances when an action costs game time.
- `move` costs walking time (about 9 tiles per second). `mine` costs hand-mining time (iron/copper ore, \
stone and coal take about 2 game seconds per unit at the start). `wait` lets time pass.
- Hand-crafting, furnaces, drills, assemblers and research progress only while game time passes \
(during moves, mining or waits).
- You must be within reach to interact: about 2.7 tiles to hand-mine resources, about 10 tiles to \
build or to use machines/chests. `find_nearest` locates things beyond your view.
- Read-only tools (get_state, find_nearest, recipe_info, technology_info) cost no game time.

Each timed action returns its result plus a compact status: level, time left, delivery progress, your \
position and inventory, and your nearby machines with their contents. Call `get_state` for full details: \
nearby resource tiles, delivery chest positions, the next level's requirements and drill drop positions. \
Tool calls also cost real money, so plan well and batch work, but the game score is what matters most."""

_XY = {"x": {"type": "number"}, "y": {"type": "number"}}
_ENTITY = {"name": {"type": "string", "description": "entity name, e.g. stone-furnace"}, **_XY}
_DIR = {"direction": {"type": "string", "enum": ["north", "east", "south", "west"]}}

TOOLS = [
    tool("get_state", "Get the scenario status and your character's state."),
    tool("move", "Walk to (x, y) (nearest free spot). Costs walking time.", _XY),
    tool("mine", "Hand-mine a resource tile (iron-ore, copper-ore, stone, coal), a tree or rock, or pick up "
         "one of your placed entities at (x, y). `count` applies to resource tiles only (1-100).",
         {"name": {"type": "string"}, **_XY, "count": {"type": "integer"}}),
    tool("place", "Place an item from your inventory as an entity at (x, y), facing `direction`.",
         {"name": {"type": "string", "description": "item name, e.g. burner-mining-drill"}, **_XY, **_DIR}),
    tool("rotate", "Set the facing direction of a placed entity.", {**_ENTITY, **_DIR}),
    tool("insert", "Insert items from your inventory into an entity (chest, furnace fuel/ore, machine "
         "ingredients, lab science packs, turret ammo).",
         {**_ENTITY, "item": {"type": "string"}, "count": {"type": "integer"}}),
    tool("take", "Take items out of an entity. With item=null, takes everything from output/chest inventories.",
         {**_ENTITY, "item": {"type": ["string", "null"]}, "count": {"type": ["integer", "null"]}}),
    tool("craft", "Queue hand-crafting of a recipe (takes game time to finish).",
         {"recipe": {"type": "string"}, "count": {"type": "integer"}}),
    tool("set_recipe", "Set the recipe of an assembling machine.", {**_ENTITY, "recipe": {"type": "string"}}),
    tool("research", "Queue a technology for research (labs need power and science packs).",
         {"technology": {"type": "string"}}),
    tool("wait", "Let game time pass (0.1-600 game seconds).", {"seconds": {"type": "number"}}),
    tool("complete_level", "Finish the current level once its requirements are met (scores the time bonus)."),
    tool("find_nearest", "Find the nearest entities of a name (or 'tree') within a radius (max 300). No time cost.",
         {"name": {"type": "string"}, "radius": {"type": "number"}, "limit": {"type": "integer"}}),
    tool("recipe_info", "Ingredients, products, crafting time and unlock status of a recipe. No time cost.",
         {"recipe": {"type": "string"}}),
    tool("technology_info", "Details of a technology, or (technology=null) the list researchable right now. "
         "No time cost.", {"technology": {"type": ["string", "null"]}}),
]

_READ_ONLY = {"find_nearest", "recipe_info", "technology_info"}


def run_supply(rcon: RCONClient, max_tool_calls: int, max_cost: float = 2.0, effort: str = "medium") -> dict[str, Any]:
    game = SupplyGame(rcon)
    game.start()
    initial = game.state()
    if "error" in initial["character"]:
        raise RuntimeError(initial["character"]["error"])
    start_tick = initial["scenario"]["tick"]
    last = {"scenario": initial["scenario"]}

    def execute(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if name == "get_state":
            state = game.state()
            last["scenario"] = state["scenario"]
            return state, True
        action = {"type": name, **{k: v for k, v in args.items() if v is not None}}
        result = game.act(action)
        ok = bool(result.get("ok"))
        if name in _READ_ONLY:
            return {"result": result}, ok
        status, last["scenario"] = game.status()
        return {"result": result, "status": status}, ok

    def is_done() -> str | None:
        s = last["scenario"]
        if s.get("game_finished"):
            return "game_over"
        if s["level"] > s["levels_total"]:
            return "all_levels_complete"
        return None

    def progress() -> str:
        s = last["scenario"]
        reqs = " ".join(f"{r['name']}={r['delivered']}/{r['required']}" for r in s["requirements"])
        return f"L{s['level']} {reqs} t_left={s.get('time_left_seconds')}s pts={s['points']}"

    try:
        tally = run_tool_loop(
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            first_message="The game is paused at the start of level 1. Here is the state:\n"
                          + json.dumps(initial, separators=(",", ":")),
            execute=execute,
            is_done=is_done,
            progress=progress,
            max_tool_calls=max_tool_calls,
            max_cost=max_cost,
            effort=effort,
        )
    finally:
        final = game.scenario()
        game.stop()

    completed = final["level"] - 1
    tally = {
        "agent": "llm-supply",
        "levels_completed": completed,
        "points": final["points"],
        "game_over": bool(final.get("game_finished")),
        "current_level": final["level"],
        "current_requirements": final["requirements"],
        "game_seconds": round((final["tick"] - start_tick) / 60, 1),
        **tally,
    }
    print(f"TALLY {tally}", flush=True)
    return tally
