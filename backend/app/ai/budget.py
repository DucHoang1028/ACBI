"""Shared deadline and actual outbound-call count for one user request."""

import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class RequestBudget:
    seconds: float = 30
    max_calls: int = 3
    calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("Request deadline reached")
        return remaining

    def consume(self) -> None:
        self.remaining()
        if self.calls >= self.max_calls:
            raise BudgetExceeded("Request call budget reached")
        self.calls += 1
