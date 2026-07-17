"""
Stage 1 quota management — provider-neutral.

Replaces the retired scrapers/groq_budget.py. Groq's binding free-tier limit was
TPM (6,000 tokens/min); Gemini's is RPM (30) and RPD (1,500), with a TPM ceiling
(250k-1M) that Stage 1's ~2k-token requests never approach. The constraint moved
from tokens to requests, so both guards here count REQUESTS.

Stage1RpmThrottle — rolling 60s requests-per-minute guard (STAGE1_RPM, default 30).
Stage1DailyQuota  — daily request ceiling (STAGE1_RPD, default 1500).
RpdExhaustedError — an RPD 429. Unlike an RPM 429 it does NOT clear with backoff;
                    the quota resets at midnight US/Pacific. Callers must stop the
                    pass cleanly rather than retry.

The published free-tier numbers vary by region, account age and billing status and
are not published per-model (Google directs you to AI Studio for your project's
live cap). So STAGE1_RPD is advisory — a real RPD 429 is the ground truth, and
mark_rpd_exhausted() lets a caller halt on it regardless of the local count.

Stage1DailyQuota deliberately keeps the retired GroqBudget's
record(prompt_tokens, completion_tokens) signature so existing call sites are
unchanged. Tokens are still accumulated for observability; only the halt decision
changed from tokens to requests.
"""

import json
import logging
import os
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

# Gemini free tier: 30 RPM / 1500 RPD (gemini-3.1-flash-lite). Both overridable —
# the live cap differs per project, so treat these as defaults, not truth.
RPM_LIMIT = int(os.getenv("STAGE1_RPM", "30"))
RPD_HARD_LIMIT = int(os.getenv("STAGE1_RPD", "1500"))
RPD_SOFT_LIMIT = int(RPD_HARD_LIMIT * 0.9)

LOG_PATH = Path(__file__).parent / "stage1_session_usage.json"


class RpdExhaustedError(Exception):
    """
    Raised on an RPD (requests-per-day) 429.

    Distinct from an RPM 429: backing off does not help — the quota resets at
    midnight US/Pacific. Callers must break their candidate loop, not continue.
    """


class Stage1DailyQuota:
    """
    Daily request ceiling for Stage 1, with token totals kept for observability.

    Per-instance and in-memory; ingestion/budget.py seeds and persists it across
    invocations so a scheduled run and a same-day manual re-run share one ceiling.
    """

    def __init__(self) -> None:
        self.tokens_used = 0
        self.calls_made = 0
        self.halted = False

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Call after every Stage 1 response. Signature kept from GroqBudget."""
        self.tokens_used += prompt_tokens + completion_tokens
        self.calls_made += 1

        if self.calls_made >= RPD_HARD_LIMIT:
            self.halted = True
            logger.warning(
                "HALT: Stage 1 daily request limit reached (%d/%d requests). "
                "Stopping run cleanly. Quota resets at midnight US/Pacific.",
                self.calls_made, RPD_HARD_LIMIT,
            )
            self.write_log()
        elif self.calls_made >= RPD_SOFT_LIMIT:
            logger.warning(
                "WARNING: Stage 1 at %d/%d requests today. Approaching daily limit.",
                self.calls_made, RPD_HARD_LIMIT,
            )

    def mark_rpd_exhausted(self) -> None:
        """
        Halt on an observed RPD 429 even if the local count is under the limit —
        the provider's cap is ground truth, ours is only an estimate.
        """
        self.halted = True
        logger.warning(
            "HALT: provider reported RPD exhausted after %d local requests "
            "(limit estimate %d). Quota resets at midnight US/Pacific.",
            self.calls_made, RPD_HARD_LIMIT,
        )
        self.write_log()

    def should_halt(self) -> bool:
        return self.halted

    def write_log(self, extra: dict = None) -> None:
        payload = {
            "tokens_used": self.tokens_used,
            "calls_made": self.calls_made,
            "halted": self.halted,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if extra:
            payload.update(extra)
        LOG_PATH.write_text(json.dumps(payload, indent=2))
        logger.info("Stage 1 session usage written to %s", LOG_PATH)


class Stage1RpmThrottle:
    """
    Rolling 60-second requests-per-minute guard.

    Usage:
        throttle = Stage1RpmThrottle()      # one per process
        throttle.wait_if_needed()           # before the call
        resp = client.models.generate_content(...)
        throttle.record()                   # after the call
    """

    WINDOW = 60.0   # rolling window width in seconds

    def __init__(self, rpm: int | None = None) -> None:
        self._rpm = rpm if rpm is not None else RPM_LIMIT
        self._window: deque = deque()   # monotonic timestamps

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.WINDOW
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    def wait_if_needed(self) -> float:
        """
        Block until the rolling window has room for one more request.
        Returns total seconds slept (0.0 if no wait was needed).
        """
        total_slept = 0.0
        while True:
            self._prune()
            if len(self._window) < self._rpm:
                return total_slept
            sleep_for = max((self._window[0] + self.WINDOW) - time.monotonic() + 0.1, 0.1)
            logger.info(
                "Stage 1 RPM: %d/%d requests in window — sleeping %.1fs for headroom",
                len(self._window), self._rpm, sleep_for,
            )
            time.sleep(sleep_for)
            total_slept += sleep_for

    def record(self) -> None:
        """Record one consumed request (call after the response, or after a 429)."""
        self._window.append(time.monotonic())
