# factorio-jev

Plumbing for an external agent to observe and control a Factorio game via
RCON, against a small fixed-goal scenario (not freeplay): **craft 50 iron
plates**.

## Pieces

- `~/Library/Application Support/factorio/scenarios/agent-env/control.lua` —
  the scenario. Spawns the player near a guaranteed iron-ore patch with a
  burner mining drill, stone furnace, and coal. Exposes a `remote` interface
  (`get_state`, `execute_action`) that the Python side calls over RCON.
- `factorio_agent/rcon_client.py` — minimal Source-RCON protocol client.
- `factorio_agent/game_client.py` — wraps RCON calls into `get_state()` /
  `execute_action(dict)` / `set_speed(n)`.
- `factorio_agent/decide.py` — placeholder decision function (walks to and
  mines the nearest iron ore). Swap this out for real agent logic later.
- `factorio_agent/loop.py` — the turn loop: get state → decide → act → repeat
  until the goal is reached or iterations run out.

## Running it

RCON only comes alive on Factorio's server component — a plain singleplayer
"Play → Scenarios" session never starts it. So this runs as a small headless
server (which loads the scenario and exposes RCON) plus a separate graphical
client that joins it over localhost multiplayer so you can still watch/play.

1. Start the headless server (runs in the foreground of this terminal):

   ```
   ./scripts/launch_server.sh
   ```

   (hardcoded dev password `agent-env-dev-password` on RCON port `27015` —
   see the script if you want to change either.)

2. In another terminal, launch the graphical client and connect to it:

   ```
   ./scripts/launch_client.sh
   ```

   This opens a normal Factorio window already joined to the server's game.

3. In a third terminal, run the driver:

   ```
   uv run python -m factorio_agent --password agent-env-dev-password --iterations 20
   ```

   Each turn prints the fetched state, the chosen action, and the action's
   result. The loop stops early if `goal_reached` becomes true (iron-plate
   count >= 50).

## Notes

- No LLM is wired in yet — `decide()` is a fixed rule so the state/action
  plumbing can be verified independently of any agent logic.
- The `move` action currently teleports the character rather than simulating
  walking; fine for exercising the loop, not meant to look like normal play.
