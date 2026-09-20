from __future__ import annotations

import argparse

from factorio_agent.game_client import GameClient
from factorio_agent.loop import run
from factorio_agent.rcon_client import RCONClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Drive the agent-env Factorio scenario over RCON.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27015)
    parser.add_argument("--password", required=True)
    parser.add_argument("--iterations", type=int, default=400)
    args = parser.parse_args()

    with RCONClient(args.host, args.port, args.password) as rcon:
        client = GameClient(rcon)
        run(client, args.iterations)


if __name__ == "__main__":
    main()
