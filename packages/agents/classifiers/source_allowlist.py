"""
Source allowlist — guardrail #2 enforcement and unapproved-outlet flagging.

The `sources` table is the operator-approved source universe, but nothing ever
checked a URL against it. Google News RSS aggregates arbitrary publishers, so
outlets nobody approved can become `source_urls` on a published incident —
8days.sg is live on one today.

Two rules, deliberately different in severity:

  signal  (EDMW/HWZ): guardrail #2 — a signal URL must NEVER be a quoted source.
          Removed unconditionally, no operator discretion.

  unknown/unapproved: NOT removed. Stripping it could take an incident's only
          source and break guardrail #1 (`source_urls` must hold >= 1 URL).
          Flagged instead, so the War Room can surface it and the operator can
          either approve the domain or re-source the story.

Matching is suffix-aware: `cnalifestyle.channelnewsasia.com` matches CNA's
registered `www.channelnewsasia.com`. A bare `www.` prefix is ignored on both
sides. Note this means a subdomain of an approved host is trusted — acceptable,
since the operator approved the parent publisher.
"""

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_cache: dict[str, dict] | None = None


def domain_of(url: str) -> str:
    """Normalised registrable host for a URL ('' when unparseable)."""
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, approved: str) -> bool:
    """True if `host` is the approved host or a subdomain of it."""
    return bool(host) and (host == approved or host.endswith("." + approved))


def load_source_domains(client=None) -> dict[str, dict]:
    """
    {normalised_domain: {"type": str, "approved": bool, "name": str}} from the
    `sources` table, cached for the life of the process (a run reads it once).

    Returns {} if the table can't be read — callers then treat everything as
    unknown, which flags rather than drops, so a DB hiccup can never silently
    strip sources.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        if client is None:
            from classifiers.corroboration import get_supabase_client
            client = get_supabase_client()
        rows = client.table("sources").select("name,url,type,approved_by_operator").execute().data or []
    except Exception as exc:
        logger.warning("source_allowlist: could not load sources table (%s) — treating all as unknown", exc)
        return {}
    _cache = {
        d: {"type": r.get("type", ""), "approved": bool(r.get("approved_by_operator")), "name": r.get("name", "")}
        for r in rows
        if (d := domain_of(r.get("url", "")))
    }
    return _cache


def classify(url: str, domains: dict[str, dict] | None = None) -> str:
    """Return 'signal' | 'approved' | 'unapproved' for a single URL."""
    domains = load_source_domains() if domains is None else domains
    host = domain_of(url)
    for approved_domain, meta in domains.items():
        if _matches(host, approved_domain):
            if meta["type"] == "signal":
                return "signal"
            return "approved" if meta["approved"] else "unapproved"
    return "unapproved"


# Both spellings are in use for the same concept: the sources table and
# scrape_edmw say 'signal', while Candidate's contract and the orchestrator say
# 'edmw' (QA M14 vocab drift). Guardrail #2 must not hinge on which one a given
# component happens to use.
SIGNAL_TYPES = frozenset({"signal", "edmw"})


def is_signal_source(source_type: str | None, url: str = "", domains: dict[str, dict] | None = None) -> bool:
    """
    True if this candidate is forum/signal material, whose URL may NEVER become a
    quoted source (guardrail #2).

    Deliberately belt-and-braces: a legal guardrail should not depend on one
    string comparison matching. Checks the declared type under BOTH vocabularies
    AND resolves the URL's domain against the sources table, so a mislabelled
    candidate from a known signal domain is still caught.

    This exact mismatch was live: scrape_edmw emits 'signal', the orchestrator
    tested == 'edmw', so an EDMW URL would have been written into source_urls.
    """
    if (source_type or "").strip().lower() in SIGNAL_TYPES:
        return True
    return bool(url) and classify(url, domains) == "signal"


def check_source_urls(urls: list[str], domains: dict[str, dict] | None = None) -> dict:
    """
    Apply the allowlist to a candidate's source_urls.

    Returns {"kept": [...], "dropped_signal": [...], "unapproved": [...]}.

    `kept` preserves order and drops only signal URLs. Unapproved URLs stay in
    `kept` AND are listed in `unapproved` for operator review — see the module
    docstring for why they are not removed.
    """
    domains = load_source_domains() if domains is None else domains
    kept, dropped_signal, unapproved = [], [], []
    for url in urls or []:
        if not url:
            continue
        verdict = classify(url, domains)
        if verdict == "signal":
            dropped_signal.append(url)
            continue
        if verdict == "unapproved":
            unapproved.append(url)
        kept.append(url)

    if dropped_signal:
        logger.warning(
            "source_allowlist: removed %d signal URL(s) from source_urls (guardrail #2): %s",
            len(dropped_signal), ", ".join(domain_of(u) for u in dropped_signal),
        )
    if unapproved:
        logger.info(
            "source_allowlist: %d source URL(s) from unapproved domain(s): %s",
            len(unapproved), ", ".join(sorted({domain_of(u) for u in unapproved})),
        )
    return {"kept": kept, "dropped_signal": dropped_signal, "unapproved": unapproved}
