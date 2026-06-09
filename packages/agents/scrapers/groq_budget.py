"""
Groq TPD budget tracker for backfill runs.
Wraps Stage 1 calls to enforce daily token ceiling.
Writes groq_session_usage.json on halt or completion.
"""

import json, time, logging
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
