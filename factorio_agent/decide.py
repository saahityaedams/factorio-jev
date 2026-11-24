"""Deterministic scripted solver for the agent-env scenario (craft 50 iron plates).

Stateless: every decision is derived from the current state, so it recovers
from failed actions or restarts without any memory. Priority order each turn:

1. Place the stone furnace (next to the character, which stands on the ore patch).
2. Collect any iron plates sitting in the furnace output.
3. Keep the furnace fuelled with coal.
4. Feed iron ore from the inventory into the furnace.
5. Hand-mine more ore if the pipeline (inventory ore + furnace ore + plates)
   is still short of the goal.
6. Otherwise wait for smelting.
"""

from __future__ import annotations

import math
from typing import Any

MINE_RANGE = 2.7  # vanilla character mining reach
MINE_BATCH = 10
FUEL_TRANSFER = 5
MIN_FUEL_BUFFER = 2


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _contents(items: Any) -> dict[str, int]:
    # Lua serialises empty tables as {} rather than [], so accept both.
    if not items:
        return {}
    return {item["name"]: item["count"] for item in items}


def _at(entity: dict[str, Any]) -> dict[str, float]:
    return {"x": entity["position"]["x"], "y": entity["position"]["y"]}


def _mine_ore(state: dict[str, Any], need: int) -> dict[str, Any]:
    position = state["position"]
    ores = [e for e in state["nearby_entities"] if e["name"] == "iron-ore"]
    if not ores:
        return {"type": "move", "x": position["x"] + 16, "y": position["y"]}
    nearest = min(ores, key=lambda e: _distance(position, e["position"]))
    if _distance(position, nearest["position"]) > MINE_RANGE:
        return {"type": "move", **_at(nearest)}
    return {"type": "mine", "name": "iron-ore", **_at(nearest), "count": min(MINE_BATCH, need)}


def decide(state: dict[str, Any]) -> dict[str, Any]:
    position = state["position"]
    inventory = state["inventory"]
    goal = state.get("goal", 50)
    ore_in_hand = inventory.get("iron-ore", 0)
    plates_in_hand = inventory.get("iron-plate", 0)

    furnaces = [e for e in state["nearby_entities"] if e["name"] == "stone-furnace"]
    if not furnaces:
        if inventory.get("stone-furnace", 0) > 0:
            return {"type": "place_entity", "name": "stone-furnace", "x": position["x"] + 2, "y": position["y"]}
        return {"type": "wait"}  # nothing sensible to do; shouldn't happen with the starter kit

    furnace = min(furnaces, key=lambda e: _distance(position, e["position"]))
    extra = furnace.get("extra") or {}
    fuel = _contents(extra.get("fuel"))
    furnace_input = _contents(extra.get("input"))
    output = _contents(extra.get("output"))
    target = {"name": "stone-furnace", **_at(furnace)}

    if output.get("iron-plate", 0) > 0:
        return {"type": "collect_from_entity", **target}

    if fuel.get("coal", 0) < MIN_FUEL_BUFFER and inventory.get("coal", 0) > 0:
        return {"type": "transfer_to_entity", **target, "item": "coal", "count": min(FUEL_TRANSFER, inventory["coal"])}

    if ore_in_hand > 0 and furnace_input.get("iron-ore", 0) < 50:
        return {"type": "transfer_to_entity", **target, "item": "iron-ore", "count": ore_in_hand}

    pipeline = ore_in_hand + furnace_input.get("iron-ore", 0) + output.get("iron-plate", 0) + plates_in_hand
    # Count the plate currently being smelted too, so we don't over-mine by one.
    if extra.get("status") == "working":
        pipeline += 1
    if pipeline < goal:
        return _mine_ore(state, goal - pipeline)

    return {"type": "wait"}
