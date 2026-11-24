# factorio-jev

Plumbing for an external agent to observe and control a Factorio game via
RCON, against a small fixed-goal scenario (not freeplay): **craft 50 iron
plates**.

## Pieces

- `scenario/agent-env/control.lua` — the scenario (source of truth; the
  launch script copies it into `~/Library/Application Support/factorio/scenarios/agent-env/`).
  Creates a script-owned character next to a guaranteed iron-ore patch with a
  burner mining drill, stone furnace, and 20 coal, so it works on a headless
  server with no players. A graphical client that joins is attached to the same
  character. Exposes a `remote` interface (`get_state`, `execute_action`).
- `config/server-settings.json` — disables `auto_pause` so the game ticks with
  nobody connected.
- `factorio_agent/rcon_client.py` — minimal Source-RCON protocol client.
- `factorio_agent/game_client.py` — wraps RCON calls into `get_state()` /
  `execute_action(dict)` / `set_speed(n)`.
- `factorio_agent/decide.py` — deterministic scripted solver (stateless; derives
  every decision from the current state).
- `factorio_agent/llm_agent.py` — Claude (`claude-opus-5-5`) playing via tool
  use: state and actions are tools; tool-call budget and USD cost cap.
- `factorio_agent/loop.py` — turn loop for the scripted agent; prints a `TALLY`.

## Actions (`execute_action`)

| type | fields | notes |
|---|---|---|
| `move` | `x, y` | teleports to the nearest free spot |
| `mine` | `name, x, y, count` | hand-mines up to 50 units instantly |
| `place_entity` | `name, x, y, direction?, exact?` | consumes the item; falls back to a nearby free spot unless `exact` |
| `transfer_to_entity` | `name, x, y, item, count` | only removes what the entity accepted |
| `collect_from_entity` | `name, x, y` | empties the output inventory |
| `craft` | `recipe, count` | hand-craft queue |
| `wait` | — | no-op; real time passes between turns |

## Running it

1. Start the headless server (foreground; leave it running):

   ```
   ./scripts/launch_server.sh
   ```

   (dev RCON password `agent-env-dev-password` on port `27015`.)

2. Optional: watch with the graphical client: `./scripts/launch_client.sh`.

3. Run an agent:

   ```
   # scripted solver (~17 s wall clock at speed 10)
   uv run python -m factorio_agent --speed 10

   # Claude via tool use (needs ANTHROPIC_API_KEY; e.g. in a gitignored .env)
   set -a; . ./.env; set +a
   uv run python -m factorio_agent --agent llm --speed 10 --iterations 60 --max-cost 2
   ```

   Restart the server to get a fresh map before each run.

## Notes

- `game.speed` does scale a dedicated server's tick rate (speed 10 gives about
  600 ticks/s), so `--speed` makes runs much faster.
- Mining and movement are instant (teleport + direct `mine_entity`); fine for
  exercising the loop, not meant to look like normal play.
- Factorio 2.1: the furnace input inventory is `defines.inventory.crafter_input`
  (`furnace_source` no longer exists).
