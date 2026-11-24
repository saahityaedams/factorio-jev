"""LLM-driven agent: Claude plays the agent-env scenario through tool use.

The game's state and actions are exposed as tools. A manual Messages API loop
(rather than the SDK tool runner) keeps per-turn control: a hard tool-call
budget, a cost cap, token accounting, and stopping the moment the goal is hit.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import anthropic

from factorio_agent.game_client import GameClient

MODEL = "claude-opus-5-5"
# $ per million tokens for claude-opus-5-5 (input, output, cache write 5m, cache read).
PRICE = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}

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


def _tool(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
    }


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


class Budget:
    def __init__(self, max_cost: float):
        self.max_cost = max_cost
        self.tokens = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}

    def add(self, usage: Any) -> None:
        self.tokens["input"] += usage.input_tokens or 0
        self.tokens["output"] += usage.output_tokens or 0
        self.tokens["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.tokens["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0

    @property
    def cost(self) -> float:
        return sum(self.tokens[k] * PRICE[k] / 1e6 for k in PRICE)

    def exceeded(self) -> bool:
        return self.cost >= self.max_cost


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
    api = anthropic.Anthropic()
    budget = Budget(max_cost)
    start = time.monotonic()
    initial = client.get_state()
    start_tick = initial["tick"]

    messages: list[dict[str, Any]] = [{
        "role": "user",
        "content": "Here is the starting state. Reach the goal.\n" + json.dumps(summarize_state(initial)),
    }]
    tool_calls = failures = api_calls = 0
    state = initial
    stop = "max_tool_calls"

    while tool_calls < max_tool_calls:
        if budget.exceeded():
            stop = "cost_cap"
            break
        response = api.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            cache_control={"type": "ephemeral"},
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        api_calls += 1
        budget.add(response.usage)
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[claude] {block.text.strip()}", flush=True)

        if response.stop_reason == "refusal":
            stop = "refusal"
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            if state.get("goal_reached"):
                stop = "goal_reached"
                break
            messages.append({"role": "user", "content": "The goal is not reached yet. Keep playing using the tools."})
            continue

        results = []
        for tu in tool_uses:
            tool_calls += 1
            try:
                payload, state = _execute(client, tu.name, dict(tu.input))
                ok = payload.get("result", {"ok": True}).get("ok", False)
            except Exception as exc:  # report game-side errors back to the model
                payload, ok = {"error": str(exc)}, False
            failures += 0 if ok else 1
            print(
                f"[call {tool_calls:3d}] {tu.name}({json.dumps(tu.input)}) -> "
                f"{json.dumps(payload.get('result', payload.get('error', 'state')))} "
                f"| plates={state.get('iron_plates')} cost=${budget.cost:.3f}",
                flush=True,
            )
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(payload),
                **({} if ok else {"is_error": True}),
            })
        messages.append({"role": "user", "content": results})

        if state.get("goal_reached"):
            stop = "goal_reached"
            break

    final = client.get_state()
    tally = {
        "agent": "llm",
        "model": MODEL,
        "effort": effort,
        "goal_reached": bool(final.get("goal_reached")),
        "iron_plates": final.get("iron_plates"),
        "stop_reason": stop,
        "tool_calls": tool_calls,
        "failed_tool_calls": failures,
        "api_calls": api_calls,
        "tokens": budget.tokens,
        "est_cost_usd": round(budget.cost, 3),
        "game_ticks": final["tick"] - start_tick,
        "wall_seconds": round(time.monotonic() - start, 1),
    }
    print(f"TALLY {tally}", flush=True)
    return tally
