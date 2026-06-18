"""
Groq quota management: daily budget ceiling + per-minute TPM rate limiter.

GroqBudget  — daily 500k token ceiling (halts the pass cleanly when hit).
GroqMinuteRateLimiter — rolling 60-second window guard for the 6k TPM free-tier
    limit. Call wait_if_needed() before each Groq request and record() after.
"""

import json, time, logging
from collections import deque
from pathlib import Path

SOFT_LIMIT = 450_000
HARD_LIMIT = 500_000
LOG_PATH = Path(__file__).parent / "groq_session_usage.json"

logger = logging.getLogger(__name__)

class GroqBudget:
    def __init__(self):
        self.tokens_used = 0
        self.calls_made = 0
        self.halted = False

    def record(self, prompt_tokens: int, completion_tokens: int):
        """Call after every Stage 1 Groq API response."""
        total = prompt_tokens + completion_tokens
        self.tokens_used += total
        self.calls_made += 1

        if self.tokens_used >= HARD_LIMIT:
            self.halted = True
            logger.warning(
                "HALT: Groq daily limit reached (%d tokens). "
                "Stopping run cleanly.", self.tokens_used
            )
            self.write_log()
        elif self.tokens_used >= SOFT_LIMIT:
            logger.warning(
                "WARNING: Groq budget at %d tokens. Approaching daily limit.",
                self.tokens_used
            )

    def should_halt(self) -> bool:
        return self.halted

    def write_log(self, extra: dict = None):
        payload = {
            "tokens_used": self.tokens_used,
            "calls_made": self.calls_made,
            "halted": self.halted,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra:
            payload.update(extra)
        LOG_PATH.write_text(json.dumps(payload, indent=2))
        logger.info("Groq session usage written to %s", LOG_PATH)


class GroqMinuteRateLimiter:
    """
    Rolling 60-second window guard for Groq's 6,000 TPM free-tier limit.

    Usage:
        limiter = GroqMinuteRateLimiter()          # one per process
        limiter.wait_if_needed(estimated_tokens)   # before the Groq call
        completion = groq_client.chat.completions.create(...)
        limiter.record(completion.usage.prompt_tokens
                       + completion.usage.completion_tokens)
    """

    TPM_SAFE = 5_500   # 500-token headroom under the 6k hard cap
    WINDOW   = 60.0    # rolling window width in seconds

    def __init__(self) -> None:
        # deque of (monotonic_timestamp: float, tokens: int)
        self._window: deque = deque()

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.WINDOW
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def wait_if_needed(self, estimated_tokens: int = 800) -> float:
        """
        Block until the rolling window has room for estimated_tokens.
        Returns total seconds slept (0.0 if no wait was needed).
        Logs each sleep so the operator can see the limiter working.
        """
        total_slept = 0.0
        while True:
            self._prune()
            used = sum(t for _, t in self._window)
            if used + estimated_tokens <= self.TPM_SAFE:
                return total_slept
            # Oldest entry determines when the window opens up
            sleep_for = max((self._window[0][0] + self.WINDOW) - time.monotonic() + 0.1, 0.1)
            logger.info(
                "Groq TPM: %d/%d tokens in window — sleeping %.1fs for headroom",
                used, self.TPM_SAFE, sleep_for,
            )
            time.sleep(sleep_for)
            total_slept += sleep_for

    def record(self, tokens: int) -> None:
        """Record actual tokens consumed after a Groq response."""
        if tokens > 0:
            self._window.append((time.monotonic(), tokens))
