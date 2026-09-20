from __future__ import annotations

import time

from factorio_agent.decide import decide
from factorio_agent.game_client import GameClient

# game.speed has no effect on a dedicated server's tick rate (only on a
# client's render smoothing), so ticks keep advancing in real time between
# turns regardless -- no pause/step needed here.
WAIT_SLEEP_SECONDS = 1.0


def run(client: GameClient, iterations: int) -> None:
    for turn in range(iterations):
        state = client.get_state()
        if state.get("goal_reached"):
            print(f"[turn {turn}] goal reached: {state['iron_plates']} iron plates")
            return

        action = decide(state)
        result = client.execute_action(action)

        print(f"[turn {turn}] iron_plates={state['iron_plates']} iron_ore={state['inventory'].get('iron-ore', 0)} action={action} result={result}")

        if action["type"] == "wait":
            time.sleep(WAIT_SLEEP_SECONDS)

    print(f"stopped after {iterations} turns without reaching the goal")
