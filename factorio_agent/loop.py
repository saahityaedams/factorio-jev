from __future__ import annotations

import collections
import time
from typing import Any, Callable

from factorio_agent.game_client import GameClient

Agent = Callable[[dict[str, Any]], dict[str, Any]]

# Real seconds to sleep after a "wait" action. game.speed *does* scale the
# dedicated server's tick rate, so at speed N one wait covers ~60*N ticks.
WAIT_SLEEP_SECONDS = 1.0


def run(client: GameClient, decide: Agent, iterations: int, verbose: bool = True) -> dict[str, Any]:
    """Turn loop: state -> decide -> act. Returns a tally of the run."""
    start = time.monotonic()
    start_tick = None
    action_counts: collections.Counter[str] = collections.Counter()
    failures = 0
    state: dict[str, Any] = {}

    for turn in range(iterations):
        state = client.get_state()
        if start_tick is None:
            start_tick = state["tick"]
        if state.get("goal_reached"):
            break

        action = decide(state)
        result = client.execute_action(action)
        action_counts[action["type"]] += 1
        if not result.get("ok"):
            failures += 1

        if verbose:
            inv = state["inventory"]
            print(
                f"[turn {turn:3d} tick {state['tick']:6d}] plates={state['iron_plates']:2d} "
                f"ore={inv.get('iron-ore', 0):2d} coal={inv.get('coal', 0):2d} "
                f"action={action} -> {result}",
                flush=True,
            )

        if action["type"] == "wait":
            time.sleep(WAIT_SLEEP_SECONDS)
    else:
        state = client.get_state()

    tally = {
        "goal_reached": bool(state.get("goal_reached")),
        "iron_plates": state.get("iron_plates"),
        "turns": sum(action_counts.values()),
        "failed_actions": failures,
        "actions": dict(action_counts),
        "game_ticks": state.get("tick", 0) - (start_tick or 0),
        "wall_seconds": round(time.monotonic() - start, 1),
    }
    print(f"TALLY {tally}", flush=True)
    return tally
