"""
Truncation guard for every Anthropic call in the pipeline.

## The problem this exists to remove

Every model call in this repo asks for JSON and parses the reply. When a reply
hits `max_tokens` the JSON is cut off mid-object, and `_parse_json` then fails
with "No JSON object in model response" — which reads exactly like the model
having returned prose. Two completely different faults, one indistinguishable
error message, and the one that is trivially fixable (raise the cap) is the one
that looks like a model problem.

`stop_reason` was read NOWHERE in the codebase before this module, so nothing
could tell them apart. Measured headroom at the time of writing, on the largest
real inputs in the archive:

    _write_draft     763 / 2048 tokens   (63% spare)
    _classify        167 /  512          (67%)
    _judge_batch     128 / 1024          (88%, against the full 53-record pool)
    _make_grouper    132 / 1024          (87%, 40 candidates)

So nothing is truncating today. The guard is here because the inputs grow —
a wider pass, a bigger cluster, a raised STAGE2_SUMMARY_RATIO — and the failure
should be legible on the day it first happens, not diagnosed from a misleading
parse error.

## Contract

`create_checked` raises TruncatedResponse instead of returning a half-object.
`create_with_headroom` adds the automatic recovery: one retry at double the cap.
Both are thin — they add a `stop_reason` check, nothing else, and pass every
other argument through untouched.
"""

import logging

logger = logging.getLogger(__name__)

# Backstop on the automatic retry. Well above anything these calls need (the
# largest observed is 763), low enough that a runaway prompt cannot bill an
# unbounded completion.
MAX_TOKENS_CEILING = 8192


class TruncatedResponse(RuntimeError):
    """
    A model reply stopped at max_tokens, so the JSON is incomplete.

    Carries the call name, model and cap, because the fix is always the same
    shape — raise that call's cap — and the message should say which one.
    """

    def __init__(self, call: str, model, max_tokens, output_tokens=None,
                 env_var: str | None = None):
        self.call = call
        self.model = model
        self.max_tokens = max_tokens
        self.output_tokens = output_tokens
        self.env_var = env_var
        fix = f" Raise {env_var} and re-run." if env_var else ""
        super().__init__(
            f"{call}: response hit max_tokens={max_tokens} on {model} "
            f"(output_tokens={output_tokens}); the JSON is incomplete and was "
            f"NOT parsed.{fix}"
        )


def create_checked(client, *, call: str, env_var: str | None = None, **kwargs):
    """
    client.messages.create(**kwargs), refusing to return a truncated reply.

    Tolerates response objects with no `stop_reason` (older SDKs, test stubs) —
    absence is treated as "not truncated", which is the pre-existing behaviour.
    """
    response = client.messages.create(**kwargs)
    if getattr(response, "stop_reason", None) == "max_tokens":
        usage = getattr(response, "usage", None)
        raise TruncatedResponse(
            call, kwargs.get("model"), kwargs.get("max_tokens"),
            getattr(usage, "output_tokens", None), env_var,
        )
    return response


def create_with_headroom(client, *, call: str, max_tokens: int,
                         env_var: str | None = None, multiplier: int = 2, **kwargs):
    """
    As create_checked, plus the recovery: ONE retry at `multiplier` x the cap.

    Returns (response, retried). Retrying is the right first move because a
    truncation means only one thing — the reply did not fit — and the cost of a
    second call is far below the cost of dropping a candidate that Stage 1 and
    the scrapers already paid for.

    Exactly one retry. If the doubled cap also truncates, the cap is not the
    problem (a runaway completion is), and TruncatedResponse propagates so the
    orchestrator's circuit breaker sees it rather than the pipeline quietly
    burning tokens in a loop.
    """
    try:
        return create_checked(client, call=call, max_tokens=max_tokens,
                              env_var=env_var, **kwargs), False
    except TruncatedResponse:
        bigger = min(max_tokens * multiplier, MAX_TOKENS_CEILING)
        if bigger <= max_tokens:
            raise
        logger.warning(
            "%s: truncated at max_tokens=%d — retrying ONCE at %d. If this "
            "recurs, raise %s permanently.", call, max_tokens, bigger,
            env_var or "the call's cap",
        )
        response = create_checked(client, call=call, max_tokens=bigger,
                                  env_var=env_var, **kwargs)
        logger.warning("%s: recovered at max_tokens=%d", call, bigger)
        return response, True
