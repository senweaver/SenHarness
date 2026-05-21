"""Iteration / depth / tool-call budgets shared across an Agent run (incl. sub-agents)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class IterationBudget:
    max_iterations: int = 12
    max_tool_calls: int = 64
    max_depth: int = 3

    _iterations: int = 0
    _tool_calls: int = 0
    _depth: int = 0

    @property
    def remaining_iterations(self) -> int:
        return max(0, self.max_iterations - self._iterations)

    def tick_iteration(self) -> bool:
        if self._iterations >= self.max_iterations:
            return False
        self._iterations += 1
        return True

    def tick_tool_call(self) -> bool:
        if self._tool_calls >= self.max_tool_calls:
            return False
        self._tool_calls += 1
        return True

    def enter_subagent(self) -> bool:
        if self._depth >= self.max_depth:
            return False
        self._depth += 1
        return True

    def exit_subagent(self) -> None:
        self._depth = max(0, self._depth - 1)
