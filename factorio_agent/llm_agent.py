"""LLM-driven agent: Claude plays the agent-env scenario (50 iron plates) through tool use."""

from __future__ import annotations

import json
import math
import time
from typing import Any

from factorio_agent.claude_loop import run_tool_loop
from factorio_agent.claude_loop import tool as _tool
from factorio_agent.game_client import GameClient

SYSTEM_PROMPT = """You are playing Factorio through a tool API. Goal: have at least 50 iron plates \
in your inventory.

Facts about this environment:
- You start standing on an iron-ore patch with 1 stone-furnace, 1 burner-mining-drill and 20 coal.
- `move` teleports you instantly. `mine` hand-mines a resource tile instantly (up to 50 units per call).
- A stone furnace needs coal as fuel and smelts 1 iron-ore into 1 iron-plate every ~3.2 game seconds.
  Its input slot holds at most 50 ore. Plates pile up in its output; use `collect_from_entity` to take them.
- Smelting only progresses while game time passes; use `wait` when there is nothing useful to do.
- Entities are addressed by name plus approximate x,y (within 2 tiles).

Be efficient: every tool call costs time and money. Batch work (mine many ore per call, feed the furnace \
in bulk) and prefer longer waits over many short ones. Each action result includes a fresh state summary, \
so you rarely need `get_state`. Stop calling tools once the goal is reached."""

_XY = {"x": {"type": "number"}, "y": {"type": "number"}}


TOOLS = [
    _tool("get_state", "Get a summary of the current game state.", {}, []),
    _tool("move", "Teleport the character to (x, y) (nearest free spot).", _XY, ["x", "y"]),
    _tool(
        "mine",
        "Hand-mine a resource tile (e.g. iron-ore, stone, coal) or pick up a placed entity at (x, y).",
        {"name": {"type": "string"}, **_XY, "count": {"type": "integer", "description": "1-50 units"}},
        ["name", "x", "y", "count"],
    ),
    _tool(
        "place_entity",
        "Place an entity from inventory at (x, y). Falls back to a nearby free spot if blocked.",
        {"name": {"type": "string"}, **_XY},
        ["name", "x", "y"],
    ),
    _tool(
        "transfer_to_entity",
        "Move items from your inventory into an entity (fuel or ingredients for a furnace).",
        {"name": {"type": "string", "description": "entity name, e.g. stone-furnace"}, **_XY,
         "item": {"type": "string"}, "count": {"type": "integer"}},
        ["name", "x", "y", "item", "count"],
    ),
    _tool(
        "collect_from_entity",
        "Take everything from an entity's output inventory (e.g. iron plates from a furnace).",
        {"name": {"type": "string"}, **_XY},
        ["name", "x", "y"],
    ),
    _tool(
        "craft",
        "Queue a hand-crafting recipe (e.g. stone-furnace from 5 stone).",
        {"recipe": {"type": "string"}, "count": {"type": "integer"}},
        ["recipe", "count"],
    ),
    _tool(
        "wait",
        "Let game time pass so furnaces/crafting progress.",
        {"game_seconds": {"type": "number", "description": "1-120 game seconds"}},
        ["game_seconds"],
    ),
]


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Compact the raw state (hundreds of ore/tree entities) into what a player needs."""
    pos = state["position"]

    def dist(e: dict[str, Any]) -> float:
        return math.hypot(e["position"]["x"] - pos["x"], e["position"]["y"] - pos["y"])

    buildings, resources = [], {}
    for e in state["nearby_entities"]:
        if e.get("type") == "resource":
            resources.setdefault(e["name"], []).append(e)
        elif e.get("type") in ("furnace", "mining-drill", "container", "assembling-machine"):
            buildings.append({"name": e["name"], "position": e["position"], **(e.get("extra") or {})})

    nearest = {
        name: {
            "tiles_in_view": len(tiles),
            "nearest": [{"x": t["position"]["x"], "y": t["position"]["y"], "dist": round(dist(t), 1)}
                        for t in sorted(tiles, key=dist)[:3]],
        }
        for name, tiles in resources.items()
    }
    return {
        "tick": state["tick"],
        "position": pos,
        "inventory": state["inventory"],
        "crafting_queue": state.get("crafting_queue") or [],
        "iron_plates": state["iron_plates"],
        "goal": state.get("goal", 50),
        "goal_reached": state["goal_reached"],
        "buildings": buildings,
        "resources": nearest,
    }


def _execute(client: GameClient, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one tool call. Returns (result payload for the model, raw state after)."""
    if name == "get_state":
        state = client.get_state()
        return {"state": summarize_state(state)}, state

    if name == "wait":
        seconds = max(1.0, min(float(args.get("game_seconds", 5)), 120.0))
        start = client.get_state()["tick"]
        target = start + int(seconds * 60)
        while True:
            state = client.get_state()
            if state["tick"] >= target:
                break
            time.sleep(0.1)
        result = {"ok": True, "waited_ticks": state["tick"] - start}
    else:
        result = client.execute_action({"type": name, **args})
        state = client.get_state()
    return {"result": result, "state": summarize_state(state)}, state


def run_llm(client: GameClient, max_tool_calls: int, max_cost: float = 2.0, effort: str = "medium") -> dict[str, Any]:
    initial = client.get_state()
    holder = {"state": initial}

    def execute(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        payload, holder["state"] = _execute(client, name, args)
        return payload, bool(payload.get("result", {"ok": True}).get("ok", False))

    tally = run_tool_loop(
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        first_message="Here is the starting state. Reach the goal.\n" + json.dumps(summarize_state(initial)),
        execute=execute,
        is_done=lambda: "goal_reached" if holder["state"].get("goal_reached") else None,
        progress=lambda: f"plates={holder['state'].get('iron_plates')}",
        max_tool_calls=max_tool_calls,
        max_cost=max_cost,
        effort=effort,
    )
    final = client.get_state()
    tally = {"agent": "llm", "goal_reached": bool(final.get("goal_reached")), "iron_plates": final.get("iron_plates"),
             "game_ticks": final["tick"] - initial["tick"], **tally}
    print(f"TALLY {tally}", flush=True)
    return tally
