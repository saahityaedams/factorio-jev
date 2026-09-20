"""Deterministic solver for the agent-env scenario (craft 50 iron plates).

Phases (inferred from state each call, with a tiny bit of module-level
memory for furnace placement retries -- fine for a fixed script solver,
this is not meant to generalize to arbitrary scenarios):

1. Mine iron ore until we have enough (TARGET_ORE).
2. Place a stone furnace (retrying nearby offsets if a spot is blocked).
3. Feed the furnace coal, then ore.
4. Wait for smelting, then collect iron plates from the furnace's output.
"""

from __future__ import annotations

import math
from typing import Any

MINE_RANGE = 1.5
TARGET_ORE = 50
FUEL_TRANSFER = 10
MIN_FUEL_BUFFER = 2

_PLACEMENT_OFFSETS = [(2, 0), (0, 2), (-2, 0), (0, -2), (3, 3), (-3, -3), (3, -3), (-3, 3)]
_memory = {"placement_attempt": 0}


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _contents_by_name(items: list[dict[str, Any]]) -> dict[str, int]:
    return {item["name"]: item["count"] for item in items}


def _mine_ore(state: dict[str, Any]) -> dict[str, Any]:
    position = state["position"]
    ores = [e for e in state["nearby_entities"] if e["name"] == "iron-ore"]

    if not ores:
        return {"type": "move", "x": position["x"] + 1, "y": position["y"]}

    nearest = min(ores, key=lambda e: _distance(position, e["position"]))
    if _distance(position, nearest["position"]) <= MINE_RANGE:
        return {"type": "mine", "name": "iron-ore", "x": nearest["position"]["x"], "y": nearest["position"]["y"]}
    return {"type": "move", "x": nearest["position"]["x"], "y": nearest["position"]["y"]}


def decide(state: dict[str, Any]) -> dict[str, Any]:
    position = state["position"]
    inventory = state["inventory"]
    ore_in_hand = inventory.get("iron-ore", 0)

    furnaces = [e for e in state["nearby_entities"] if e["name"] == "stone-furnace"]

    if not furnaces:
        if ore_in_hand < TARGET_ORE:
            return _mine_ore(state)

        attempt = _memory["placement_attempt"]
        _memory["placement_attempt"] += 1
        dx, dy = _PLACEMENT_OFFSETS[attempt % len(_PLACEMENT_OFFSETS)]
        return {
            "type": "place_entity",
            "name": "stone-furnace",
            "x": position["x"] + dx,
            "y": position["y"] + dy,
        }

    furnace = furnaces[0]
    extra = furnace.get("extra") or {"input": [], "fuel": [], "output": []}
    fuel = _contents_by_name(extra["fuel"])
    furnace_input = _contents_by_name(extra["input"])
    output = _contents_by_name(extra["output"])

    if output.get("iron-plate", 0) > 0:
        return {"type": "collect_from_entity", "name": "stone-furnace", "x": furnace["position"]["x"], "y": furnace["position"]["y"]}

    if fuel.get("coal", 0) < MIN_FUEL_BUFFER and inventory.get("coal", 0) > 0:
        count = min(FUEL_TRANSFER, inventory["coal"])
        return {
            "type": "transfer_to_entity",
            "name": "stone-furnace",
            "x": furnace["position"]["x"],
            "y": furnace["position"]["y"],
            "item": "coal",
            "count": count,
        }

    if furnace_input.get("iron-ore", 0) < 1 and ore_in_hand > 0:
        return {
            "type": "transfer_to_entity",
            "name": "stone-furnace",
            "x": furnace["position"]["x"],
            "y": furnace["position"]["y"],
            "item": "iron-ore",
            "count": ore_in_hand,
        }

    return {"type": "wait"}
