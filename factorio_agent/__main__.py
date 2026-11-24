from __future__ import annotations

import argparse

from factorio_agent.game_client import GameClient
from factorio_agent.loop import run
from factorio_agent.rcon_client import RCONClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Drive the agent-env Factorio scenario over RCON.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27015)
    parser.add_argument("--password", default="agent-env-dev-password")
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--speed", type=float, default=1.0, help="game.speed to set before running")
    parser.add_argument("--agent", choices=["scripted", "llm", "supply"], default="scripted")
    parser.add_argument("--max-cost", type=float, default=2.0, help="LLM agent: stop once estimated spend hits this (USD)")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"], help="LLM agent effort")
    args = parser.parse_args()

    with RCONClient(args.host, args.port, args.password) as rcon:
        if args.agent == "supply":
            # Built-in Supply challenge (scripts/launch_supply.sh + graphical client).
            from factorio_agent.supply import run_supply

            run_supply(rcon, args.iterations, max_cost=args.max_cost, effort=args.effort)
            return
        client = GameClient(rcon)
        client.set_speed(args.speed)
        try:
            if args.agent == "scripted":
                from factorio_agent.decide import decide

                run(client, decide, args.iterations)
            else:
                from factorio_agent.llm_agent import run_llm

                run_llm(client, args.iterations, max_cost=args.max_cost, effort=args.effort)
        finally:
            client.set_speed(1.0)


if __name__ == "__main__":
    main()
