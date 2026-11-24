# factorio-jev

Let an AI (or a script) play **Factorio** from outside the game, and measure how well it does.

A Python program connects to a running Factorio server, reads the game state, and sends actions
("walk here", "mine 10 iron ore", "put coal in that furnace"). The actions are decided either
by a fixed script or by **Claude** using tool calls. The goal is to see how good Claude is at
Factorio's production challenges, using the game's own built-in scoring wherever possible.

---

## How it fits together

```
 ┌──────────────────────────┐     RCON (TCP 27015)     ┌──────────────────────────────────┐
 │  Python (this repo)      │ ───────────────────────▶ │  Factorio headless server        │
 │                          │   "/sc remote.call(...)" │                                  │
 │  • scripted agent        │ ◀─────────────────────── │  • scenario (the map + rules)    │
 │  • Claude agent          │        JSON replies      │  • agent-api mod / scenario Lua  │
 │    (Anthropic SDK)       │                          │    = the "remote interface"      │
 └────────────┬─────────────┘                          └───────────────┬──────────────────┘
              │ tool calls                                             │ multiplayer (localhost)
              ▼                                                        ▼
       Claude API (claude-opus-5-5)                     Factorio graphical client (optional to
                                                         watch; required for Supply challenge)
```

The main ideas, in plain terms:

1. **RCON** is a remote-console protocol the Factorio *server* supports. You send a console
   command such as `/sc <lua code>` and get the printed output back. `factorio_agent/rcon_client.py`
   is a small client for it.
2. **A remote interface** is a set of Lua functions inside the game (`get_state`, `execute_action`)
   that RCON commands can call. They return JSON, so Python can read the game and act on it.
3. **An agent** runs a loop: *read state → decide → act → repeat*. The decision comes either
   from a hand-written rule set or from Claude.
4. **Claude plays through tools.** Every game action is a tool (`move`, `mine`, `place`,
   `insert`, `craft`, ...). Claude calls tools, sees the results, and keeps going until the
   goal is met or its budget runs out.

---

## The two test setups

### A. `agent-env`: our own small scenario (goal: 50 iron plates)

A custom scenario (`scenario/agent-env/control.lua`) built to test the plumbing. You spawn on an
iron-ore patch with a stone furnace, a burner drill and 20 coal. The goal is 50 iron plates in
your inventory.

- **No player needed.** The scenario creates its own character, so it runs on a headless
  server with nobody connected.
- **Relaxed rules.** Movement is a teleport and mining is instant. It's good for testing the
  pipeline, not for judging skill.

### B. Supply challenge: Factorio's built-in scenario (the real test)

`base/supply` ships with Factorio. It has **21 timed levels**. Each level asks you to deliver
specific items into red chests near spawn before a timer runs out:

| Level | Requirement (time limit) |
|---|---|
| 1 | 15 stone (336 s) |
| 2 | 30 iron plates (420 s) |
| 3 | 30 iron + 30 copper plates (420 s) |
| 4 | 30 iron plates + 30 iron gear wheels (420 s) |
| 5 | 40 iron plates + 30 gears + 40 copper cable (420 s) |
| ... | up to science packs at level 21 |

Finishing a level early (the **Next level** button) turns unused time into bonus points. If the
timer runs out before the requirements are met, the game is over. **Score = levels completed +
points**, as calculated by the scenario itself.

We can't edit the built-in scenario, so the controls live in a **mod**
(`mods/agent-api/`). A mod's script runs alongside any scenario. Supply challenge sets
itself up when a real player joins, so the **graphical client must be connected**, and the
agent controls that player's character.

#### Making it a fair test

| Rule | Why |
|---|---|
| **Turn-based clock.** The game is paused while Claude thinks and advances only when an action takes time. | Measures decision quality, not API latency. |
| **Actions cost real game time.** Walking ≈ 9 tiles/s (`distance / running speed`), hand-mining ≈ 2 s per ore (`mining time / mining speed`), crafting/smelting/research at normal speed. | Teleporting or instant mining would make timed levels trivial. |
| **Reach limits.** About 2.7 tiles to hand-mine and 10 tiles to build or use machines. | Same as a normal player. |
| **Level 1's timer starts when the agent starts.** | The server runs about 15 s before anyone can connect. |
| **"Next level" is pressed through the scenario's own handler.** | Points and bonuses come from the scenario's code, not ours. |
| **$ cap per run** (default $2). | Comparable runs, bounded spend. |

---

## Quick start

**Needs:** macOS with Factorio at `/Applications/factorio.app` (2.1, base game),
[uv](https://docs.astral.sh/uv/), and an Anthropic API key for the Claude agents.

```bash
# API key for the Claude agents (gitignored)
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

### Run A: the 50-plate scenario

```bash
./scripts/launch_server.sh                 # terminal 1: headless server (leave running)

uv run python -m factorio_agent --speed 10                    # scripted agent (~17 s)

set -a; . ./.env; set +a                   # Claude agent
uv run python -m factorio_agent --agent llm --speed 10 --iterations 60 --max-cost 2
```

### Run B: Supply challenge

```bash
./scripts/launch_supply.sh                 # terminal 1: server with the agent-api mod
./scripts/launch_client.sh                 # terminal 2: graphical client joins (required)

set -a; . ./.env; set +a                   # terminal 3: Claude plays
uv run python -m factorio_agent --agent supply --iterations 400 --max-cost 2
```

Restart the server for a fresh map before each run. The run prints one line per tool call and
ends with a `TALLY {...}` line (levels completed, points, cost, tokens). Save it with
`> logs/<name>.log`.

**Options:** `--max-cost` (USD cap), `--iterations` (max tool calls), `--effort`
(`low`…`max`, default `medium`), `--speed` (game speed for the agent-env runs).

---

## What's in the repo

| Path | What it is |
|---|---|
| `factorio_agent/rcon_client.py` | Minimal RCON protocol client. |
| `factorio_agent/game_client.py` | `get_state()` / `execute_action()` for the agent-env scenario. |
| `factorio_agent/decide.py` | Scripted solver for agent-env (decides from the current state each turn). |
| `factorio_agent/loop.py` | Turn loop for the scripted agent. |
| `factorio_agent/claude_loop.py` | Shared Claude tool-use loop: budget, cost cap, token accounting. |
| `factorio_agent/llm_agent.py` | Claude agent for agent-env. |
| `factorio_agent/supply.py` | Supply challenge harness: scenario progress, level completion, Claude tools and prompt. |
| `mods/agent-api/` | Factorio mod: player controls with game-time costs, for any scenario. |
| `scenario/agent-env/control.lua` | The 50-plate scenario (copied into Factorio's folder at launch). |
| `config/server-settings.json` | Server settings (no auto-pause, LAN-only). |
| `scripts/` | `launch_server.sh` (agent-env), `launch_supply.sh` + `launch_client.sh` (Supply), `build_mods.sh`. |

### Actions available to the Supply agent (tools)

| Tool | Game time? | What it does |
|---|---|---|
| `move(x, y)` | walking time | Walk to a point. |
| `mine(name, x, y, count)` | mining time | Hand-mine ore/stone/coal, a tree or rock, or pick up a building. |
| `place(name, x, y, direction)` | – | Place a building from inventory. |
| `rotate`, `set_recipe`, `research` | – | Turn a building, set an assembler recipe, queue research. |
| `insert(...)`, `take(...)` | – | Move items into or out of buildings (fuel, ore, plates, chests). |
| `craft(recipe, count)` | progresses with time | Queue hand-crafting. |
| `wait(seconds)` | yes | Let time pass so furnaces and crafting progress. |
| `complete_level()` | – | Press **Next level** (only when requirements are met). |
| `get_state`, `find_nearest`, `recipe_info`, `technology_info` | – | Read-only lookups. |

After each timed action Claude gets a **short status**: level, time left, delivery progress,
position, inventory, and a one-line summary of each nearby machine. Full detail comes from
`get_state`. This keeps the conversation small, which matters because every request re-sends
it.

---

## Results (2026-11-24)

All Claude runs use `claude-opus-5-5` at medium effort. Costs are estimates from token counts.
Logs are in `logs/` (gitignored).

### A. agent-env: 50 iron plates

| Agent | Runs | Result | Actions | Game time | Cost |
|---|---|---|---|---|---|
| Scripted | 4 fresh maps | **4/4 reached 50 plates**, 0 failed actions | 45–47 turns | ~9,900 ticks (2.75 min) | – |
| Claude | 1 fresh map | **Reached 51 plates**, 0 failed actions | 12 tool calls (4 API calls) | 20,868 ticks (5.8 min) | **$0.07** |

Claude needed far fewer actions than the script: it batched mining 50 ore at a time and issued
several calls per response. It used about twice the game time, though, because it filled the
furnace once and waited rather than feeding it while mining. This scenario has relaxed rules
(teleport, instant mining), so it mainly shows that the pipeline works end to end.

### B. Supply challenge: $2 per run

| Run | Change | Levels completed | Points | Tool calls | Cost | Ended because |
|---|---|---|---|---|---|---|
| 1 | first version | **3** | 3,583 | 142 | $2.02 | cost cap (at the start of level 4) |
| 2 | trimmed per-call payload | **4** | 4,667 | 226 | $2.00 | cost cap (90 s into level 5) |

How long each level took (game seconds used / time limit):

| Level | Run 1 | Run 2 |
|---|---|---|
| 1: 15 stone | 79 / 336 s | 110 / 336 s |
| 2: 30 iron plates | 117 / 420 s | 180 / 420 s |
| 3: 30 iron + 30 copper | 216 / 420 s | 218 / 420 s |
| 4: 30 iron + 30 gears | – | 65 / 420 s |
| 5: 40 iron + 30 gears + 40 cable | – | (stopped by cost cap after 90 s) |

**What we saw**

- **Claude never failed a level.** Every level it started, it finished with time to spare. Both
  runs ended on the money cap, not on game over. Over the levels it reached, the binding limit
  was budget, not skill.
- **It plays sensibly.** It hand-mined stone for level 1, then crafted stone furnaces, built
  burner mining drills that feed furnaces directly, fuelled everything with coal, collected
  plates and delivered them. It pressed **Next level** as soon as the requirements were met, to
  bank the time bonus.
- **Few mistakes.** 4–5% of tool calls failed (run 1: 5 of 142; run 2: 12 of 226). Examples:
  guessing the entity name `rock` instead of `big-rock`, mining from just outside reach, crafting
  without enough ingredients. Each failure returns a clear error, and it recovered every time.
- **Cost is mostly re-reading history.** In run 1, cache reads and writes were about 78% of the
  spend, because each action returned the full state and every request re-sends the whole
  conversation. Run 2 returns a short status instead (about 2,000 → 450 characters per action).
  That reached each level for much less money and got one level further on the same $2. In
  run 2 history was about 65% of the cost (cache reads $0.80, cache writes $0.51, Claude's own
  output $0.69). Further trimming would help, but output is now a large share too.
- **Level 4 was its fastest level.** Its drills and furnaces were already producing plates, so
  it only had to hand-craft 30 gear wheels and deliver them (65 s of 420 s). The infrastructure
  it built early paid off.

| Point in the run | Run 1 cost | Run 2 cost |
|---|---|---|
| Level 1 done | $0.08 | $0.07 |
| Level 2 done | $0.54 | $0.39 |
| Level 3 done | $1.88 | $1.06 |
| Level 4 done | – | $1.45 |

### Caveats

- Two Supply runs is a small sample, and map spawn and resource layouts vary between runs.
  More runs are needed for a reliable number.
- Movement is still a short "walk" (teleport with walking-time cost). There's no pathfinding
  around obstacles.
- The supply harness pauses the game between actions, so this measures planning, not
  real-time play.
- The later levels (circuits, science packs) need power, research and assemblers. The tools
  exist (`place`, `set_recipe`, `research`), but no run has reached those levels yet.

---

## Troubleshooting

- **`no connected player with a character`**: Supply challenge needs the graphical client
  connected (`./scripts/launch_client.sh`).
- **`non-JSON RCON response: Cannot execute command...`**: a Lua error. The message includes
  the stack trace.
- **Client can't join**: both server and client must load the same mod folder (`.run/mods`,
  built by `scripts/build_mods.sh`). Restart both after changing the mod.
- **Factorio 2.1 API notes**: furnace input is `defines.inventory.crafter_input`, and inventory
  ids overlap (fuel = chest = turret ammo = 1), so the mod picks inventories per entity type.
  Some 2.x technologies (e.g. `steam-power`) unlock by doing something rather than by research;
  `technology_info` reports that trigger.
