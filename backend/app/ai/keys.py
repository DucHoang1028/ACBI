"""Several provider keys used one at a time: an error moves the request to the next."""

import threading
import time
from collections import deque
from dataclasses import dataclass, field

# Seconds a key rests after a failure, by cause.
REST_AUTH = 3600.0  # 401/403: the key itself is unusable
REST_SERVER = 30.0  # 5xx or transport trouble
REST_RATE_DEFAULT = 60.0  # 429 without Retry-After


@dataclass
class KeyState:
    """One key with its own local rate window, so keys never share quotas."""

    key: str
    label: str
    rpm: int
    tpm: int
    calls: deque[list[float]] = field(default_factory=deque)
    rest_until: float = 0.0
    # Provider details; an empty url means Groq with the client's own model.
    url: str = ""
    model: str = ""
    json_object: bool = False  # no strict schemas: the schema goes in the prompt
    extra: dict[str, object] = field(default_factory=dict)


class KeyPool:
    def __init__(self, keys: list[str], rpm: int, tpm: int):
        unique = list(dict.fromkeys(k.strip() for k in keys if k.strip()))
        self.states = [
            KeyState(key=k, label=f"key{i + 1}", rpm=rpm, tpm=tpm)
            for i, k in enumerate(unique)
        ]
        self.current = 0
        self.lock = threading.Lock()

    @classmethod
    def of(cls, states: list[KeyState]) -> "KeyPool":
        """A pool over ready-made keys, e.g. several providers in one order."""
        pool = cls([], 1, 1)
        pool.states = states
        return pool

    def __len__(self) -> int:
        return len(self.states)

    def available(self, estimated: int) -> list[KeyState]:
        """Usable keys, the current one first; each has room in its local window."""
        now = time.monotonic()
        ordered = self.states[self.current :] + self.states[: self.current]
        usable: list[KeyState] = []
        with self.lock:
            for state in ordered:
                while state.calls and state.calls[0][0] <= now - 60:
                    state.calls.popleft()
                if (
                    now >= state.rest_until
                    and len(state.calls) < state.rpm
                    and sum(n for _, n in state.calls) + estimated <= state.tpm
                ):
                    usable.append(state)
        return usable

    def wait_time(self, estimated: int) -> float:
        """Seconds until some key has room again (infinite if none ever will)."""
        now = time.monotonic()
        best = float("inf")
        with self.lock:
            for state in self.states:
                moments = [state.rest_until, *(at + 60 for at, _ in state.calls)]
                for moment in sorted(max(m, now) for m in moments):
                    live = [n for at, n in state.calls if at > moment - 60]
                    if (
                        moment >= state.rest_until
                        and len(live) < state.rpm
                        and sum(live) + estimated <= state.tpm
                    ):
                        best = min(best, moment - now)
                        break
        return best

    def reserve(self, state: KeyState, estimated: int) -> list[float]:
        reservation = [time.monotonic(), float(estimated)]
        with self.lock:
            state.calls.append(reservation)
        return reservation

    def succeeded(
        self,
        state: KeyState,
        reservation: list[float],
        tokens: float | None,
        rpm: int | None,
        tpm: int | None,
    ) -> None:
        with self.lock:
            # Providers count reserved output tokens, so never release below the
            # estimate: it keeps the local window honest about the provider's.
            if tokens is not None:
                reservation[1] = max(reservation[1], float(tokens))
            # Trust the provider's stated limits when they are lower than ours.
            if rpm:
                state.rpm = min(state.rpm, rpm)
            if tpm:
                state.tpm = min(state.tpm, tpm)
            self.current = self.states.index(state)

    def failed(
        self, state: KeyState, status: int | None, retry_after: float | None
    ) -> None:
        """Rest the key and make the next one current."""
        if status in (401, 403):
            rest = REST_AUTH
        elif status == 429:
            rest = max(1.0, retry_after or REST_RATE_DEFAULT)
        else:
            rest = REST_SERVER
        with self.lock:
            state.rest_until = time.monotonic() + rest
            self.current = (self.states.index(state) + 1) % len(self.states)
