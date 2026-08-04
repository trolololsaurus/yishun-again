"""
Deduplicator (INGESTION_DESIGN.md §5.2) — URL-exact only in v1 (no fuzzy/title
dedup, deferred per §9).

Checks both the existing-record dedup (war_room_queue.source_url and
incidents.source_urls, same canonical checks as classifiers.corroboration
.check_duplicate) and in-pass dedup against candidates already seen earlier in
this run.

Review S1 (infra-failure handling): classifiers.corroboration.check_duplicate
deliberately fails OPEN (returns False) on a Supabase error — correct for its
existing single-lookup callers (backfill_agent.py), where treating an
unverifiable item as "novel" is an acceptable conservative default. At pass
scale that default is dangerous: during an outage EVERY candidate would look
novel, and the queue-writes that follow would also fail. This module performs
the same canonical-URL checks but raises InfraError instead of failing open —
the orchestrator MUST catch InfraError and abort the whole pass as DEGRADED
(IngestionReport.infra_error), not continue treating everything as novel.
"""

import logging

from ingestion.contracts import Candidate

logger = logging.getLogger(__name__)


class InfraError(Exception):
    """
    Raised when novelty could not be determined because the database is
    unreachable — distinct from "checked, and it is not a duplicate".
    """


def is_duplicate(candidate: Candidate, client, seen_urls: set[str] | None = None) -> bool:
    """
    Return True if `candidate.url` is a duplicate — either already present in
    war_room_queue / incidents, or already seen earlier in this pass.

    Args:
        candidate: the Candidate being checked.
        client:    Supabase admin client (required — no silent client
                   creation here; an unconfigured client is itself an infra
                   failure the orchestrator should have caught earlier).
        seen_urls: in-pass dedup set (§5.2) — candidates are deduped against
                   each other by URL within a single pass, not just against
                   the DB. Caller is responsible for adding novel URLs to
                   this set as the pass proceeds.

    Raises:
        InfraError: if the underlying Supabase queries fail. Callers MUST NOT
            treat this as "not a duplicate" (§5.2 review S1).
    """
    from classifiers.source_allowlist import canonical_url

    url = candidate.url
    canon = canonical_url(url)

    # In-pass dedup compares CANONICAL forms. Matching raw strings let the same
    # article through twice when a listing linked it with a tracking parameter
    # and the article page linked it without — which is exactly how
    # `yishun-python-escapes-drain-worksite-aug-2026` published holding one
    # Stomp report twice and advertising "2 sources".
    if seen_urls is not None and canon in seen_urls:
        return True

    # Both spellings are checked against the DB. PostgREST matches the stored
    # string exactly, so a row written before canonicalisation still carries its
    # tracking parameter and would be missed by a canonical-only lookup. The
    # second pair of queries only runs when the two forms actually differ.
    candidates_to_check = [url] if canon == url else [url, canon]

    try:
        for probe in candidates_to_check:
            result = (
                client.table("war_room_queue")
                .select("id")
                .eq("source_url", probe)
                .limit(1)
                .execute()
            )
            if result.data:
                return True

            result = (
                client.table("incidents")
                .select("id")
                .contains("source_urls", [probe])
                .limit(1)
                .execute()
            )
            if result.data:
                return True
        return False

    except Exception as exc:
        raise InfraError(f"Duplicate check failed for {url!r}: {exc}") from exc
