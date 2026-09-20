"""Talks to the `agent` remote interface exposed by the agent-env scenario's
control.lua, over RCON."""

from __future__ import annotations

import json
from typing import Any

from factorio_agent.rcon_client import RCONClient


def _lua_string_literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class GameClient:
    def __init__(self, rcon: RCONClient):
        self._rcon = rcon

    def get_state(self) -> dict[str, Any]:
        response = self._rcon.send_command('/sc rcon.print(remote.call("agent", "get_state"))')
        return json.loads(response)

    def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        action_json = _lua_string_literal(json.dumps(action))
        command = f'/sc rcon.print(remote.call("agent", "execute_action", {action_json}))'
        response = self._rcon.send_command(command)
        return json.loads(response)

    def set_speed(self, speed: float) -> None:
        self._rcon.send_command(f"/sc game.speed = {speed}")
