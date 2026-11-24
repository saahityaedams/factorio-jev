"""Shared Claude tool-use loop used by every scenario agent.

A manual Messages API loop (rather than the SDK tool runner) for per-turn
control: tool-call budget, USD cost cap, token accounting, and a scenario
supplied `done` check after every tool call.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

MODEL = "claude-opus-5-5"
# $ per million tokens for claude-opus-5-5 (input, output, cache write 5m, cache read).
PRICE = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}


def tool(name: str, description: str, props: dict[str, Any] | None = None,
         required: list[str] | None = None) -> dict[str, Any]:
    props = props or {}
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": required if required is not None else list(props),
            "additionalProperties": False,
        },
    }


@dataclass
class Budget:
    max_cost: float
    tokens: dict[str, int] = field(default_factory=lambda: {k: 0 for k in PRICE})

    def add(self, usage: Any) -> None:
        self.tokens["input"] += usage.input_tokens or 0
        self.tokens["output"] += usage.output_tokens or 0
        self.tokens["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.tokens["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0

    @property
    def cost(self) -> float:
        return sum(self.tokens[k] * PRICE[k] / 1e6 for k in PRICE)


# execute(name, input) -> (payload for the model, ok flag)
Executor = Callable[[str, dict[str, Any]], tuple[dict[str, Any], bool]]


def run_tool_loop(
    *,
    system: str,
    tools: list[dict[str, Any]],
    first_message: str,
    execute: Executor,
    is_done: Callable[[], str | None],
    progress: Callable[[], str],
    max_tool_calls: int,
    max_cost: float,
    effort: str = "medium",
    log: Callable[[str], None] = lambda s: print(s, flush=True),
) -> dict[str, Any]:
    """Run Claude against the tools until is_done() returns a reason, or a budget runs out."""
    api = anthropic.Anthropic()
    budget = Budget(max_cost)
    start = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": first_message}]
    tool_calls = failures = api_calls = 0
    stop = "max_tool_calls"

    while tool_calls < max_tool_calls:
        if budget.cost >= max_cost:
            stop = "cost_cap"
            break
        response = api.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
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
                log(f"[claude] {block.text.strip()}")

        if response.stop_reason == "refusal":
            stop = "refusal"
            break
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            reason = is_done()
            if reason:
                stop = reason
                break
            messages.append({"role": "user", "content": "The game is not over yet. Keep playing using the tools."})
            continue

        results = []
        for tu in tool_uses:
            tool_calls += 1
            try:
                payload, ok = execute(tu.name, dict(tu.input))
            except Exception as exc:  # surface harness/game errors to the model instead of crashing
                payload, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
            failures += 0 if ok else 1
            shown = payload.get("result", payload.get("error", ""))
            log(f"[call {tool_calls:3d}] {tu.name}({json.dumps(tu.input)}) -> {json.dumps(shown)[:300]} "
                f"| {progress()} | ${budget.cost:.3f}")
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(payload, separators=(",", ":")),
                **({} if ok else {"is_error": True}),
            })
        messages.append({"role": "user", "content": results})

        reason = is_done()
        if reason:
            stop = reason
            break

    return {
        "model": MODEL,
        "effort": effort,
        "stop_reason": stop,
        "tool_calls": tool_calls,
        "failed_tool_calls": failures,
        "api_calls": api_calls,
        "tokens": budget.tokens,
        "est_cost_usd": round(budget.cost, 3),
        "wall_seconds": round(time.monotonic() - start, 1),
    }
