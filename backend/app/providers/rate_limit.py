"""Provider-agnostic client-side rate limiting, shared by every external API we call
(Gemini embeddings today; Groq / Gemini chat later).

A provider never hard-codes limits. It asks for a limiter by key (`get_limiter("gemini_embedding")`),
whose requests-per-minute and tokens-per-minute come from settings (`{KEY}_RPM`, `{KEY}_TPM` in .env,
0 = no limit). One limiter per key is shared process-wide, so every caller using the same API key
draws from the same window."""
import asyncio
import logging
import time
from collections import deque

from app.config import settings

log = logging.getLogger("devdocs.ratelimit")

# One batch may use at most this share of a minute's token budget, so batches stay small enough to
# interleave with other callers instead of emptying the whole window in one go.
BATCH_TOKEN_FRACTION = 0.25


def estimate_tokens(text: str) -> int:
    """Cheap approximation (about 4 characters per token); good enough for pacing."""
    return max(1, len(text) // 4)


class SlidingWindowLimiter:
    """At most `limit` units of cost in any rolling `window` seconds; `acquire` waits when full.
    A limit of 0 disables it. A single cost above the limit is clamped so it can still go through alone."""

    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic, sleep=asyncio.sleep):
        self.limit, self.window, self._clock, self._sleep = limit, window, clock, sleep
        self._events: deque[list] = deque()  # [time, cost]; lists so `settle` can correct a cost in place
        self._used = 0
        self._lock = asyncio.Lock()

    def _expire(self, now: float) -> None:
        while self._events and self._events[0][0] <= now - self.window:
            self._used -= self._events.popleft()[1]

    def wait_estimate(self, cost: int) -> float:
        """Seconds `acquire(cost)` would wait right now (0 = immediately). Does not reserve anything."""
        if self.limit <= 0:
            return 0.0
        cost = min(max(cost, 1), self.limit)
        now = self._clock()
        self._expire(now)
        used = self._used
        if used + cost <= self.limit:
            return 0.0
        for t, c in self._events:  # room appears as the oldest events leave the window
            used -= c
            if used + cost <= self.limit:
                return max(t + self.window - now, 0.0)
        return 0.0

    async def acquire(self, cost: int) -> list | None:
        """Waits for room, reserves `cost` and returns the reservation (pass it to `settle`)."""
        if self.limit <= 0:
            return None
        cost = min(max(cost, 1), self.limit)
        async with self._lock:  # one waiter at a time keeps the order fair
            while True:
                now = self._clock()
                self._expire(now)
                if self._used + cost <= self.limit:
                    entry = [now, cost]
                    self._events.append(entry)
                    self._used += cost
                    return entry
                wait = self._events[0][0] + self.window - now
                log.warning("rate limiter: waiting %.1fs to stay under the provider quota", wait)
                await self._sleep(max(wait, 0.05))

    def settle(self, entry: list | None, actual_cost: int) -> None:
        """Replace a reservation's estimated cost with the real one (a stream's cost is only known afterwards).
        A reservation that already left the window is ignored."""
        if entry is None or self.limit <= 0 or not any(e is entry for e in self._events):
            return
        actual = max(int(actual_cost), 0)
        self._used += actual - entry[1]
        entry[1] = actual


class RateLimiter:
    """Requests-per-minute and tokens-per-minute for one API key. Either may be 0 (unlimited)."""

    def __init__(
        self, requests_per_minute: int = 0, tokens_per_minute: int = 0,
        clock=time.monotonic, sleep=asyncio.sleep,
    ):
        self.requests_per_minute, self.tokens_per_minute = requests_per_minute, tokens_per_minute
        self._requests = SlidingWindowLimiter(requests_per_minute, 60.0, clock, sleep)
        self._tokens = SlidingWindowLimiter(tokens_per_minute, 60.0, clock, sleep)

    @property
    def max_batch_tokens(self) -> int:
        """Token cap for one batch, or 0 when tokens are not limited."""
        return int(self.tokens_per_minute * BATCH_TOKEN_FRACTION)

    async def acquire(self, requests: int = 1, tokens: int = 0) -> list | None:
        """Waits for room in both windows. Returns the token reservation (for `settle`), if tokens were asked."""
        await self._requests.acquire(requests)
        if tokens:
            return await self._tokens.acquire(tokens)
        return None

    def settle(self, reservation: list | None, actual_tokens: int) -> None:
        self._tokens.settle(reservation, actual_tokens)

    def wait_estimate(self, requests: int = 1, tokens: int = 0) -> float:
        return max(self._requests.wait_estimate(requests), self._tokens.wait_estimate(tokens) if tokens else 0.0)


def batch_by_budget(texts: list[str], max_items: int, max_tokens: int = 0) -> list[list[str]]:
    """Split texts, in order, into batches of at most `max_items` and (when `max_tokens` > 0) at most
    `max_tokens` estimated tokens. A single text over the token cap forms a batch of its own."""
    batches: list[list[str]] = []
    current: list[str] = []
    tokens = 0
    for text in texts:
        n = estimate_tokens(text)
        over_tokens = max_tokens > 0 and bool(current) and tokens + n > max_tokens
        if current and (len(current) >= max_items or over_tokens):
            batches.append(current)
            current, tokens = [], 0
        current.append(text)
        tokens += n
    if current:
        batches.append(current)
    return batches


_limiters: dict[str, RateLimiter] = {}


def get_limiter(key: str) -> RateLimiter:
    """The shared limiter for a provider key such as `gemini_embedding` or `groq_llm`."""
    if key not in _limiters:
        _limiters[key] = RateLimiter(
            int(getattr(settings, f"{key}_rpm", 0)), int(getattr(settings, f"{key}_tpm", 0)),
        )
    return _limiters[key]


def reset_limiters() -> None:
    """Forget cached limiters (tests, or after settings change)."""
    _limiters.clear()
