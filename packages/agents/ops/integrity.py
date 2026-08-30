"""
Integrity agent (req #10) — "check for double entries from the same sources and
hallucinating agents and correct them".

## Why `apply` defaults to False

This is the only agent in the fleet whose job is to rewrite rows another agent
already wrote and — in the published case — a human already approved. That makes
its worst failure mode strictly worse than the bugs it hunts. A duplicate draft
is visible, inert and reversible. A wrong "correction" applied to a live
incident is none of those: it reads as editorial intent, nobody knows to check
it, and the original value is gone.

So the default pass is READ-ONLY. It records every finding in agent_events and
mails the operator at most once a day. `apply=True` performs only the two
corrections whose blast radius is provably bounded:

  * recompute a drifted `corroboration_count` from the row's own source_urls —
    a derived integer, no editorial content, and demonstrably wrong today
    (QA H8: merges never bumped it, so the lightning meter under-counts).
  * dismiss an UNPROCESSED war_room_queue duplicate — nothing published, no
    reader ever saw it, and consolidation/check.py would have returned 'skip'
    for it had the pass run in a different order.

Everything else is reported, never written: which of two PUBLISHED incidents is
the real one, a dead source URL, a wrong incident_date, a slug that contradicts
its date. Those all need a human, because the fix requires knowing what the
story actually was — information this agent does not have.

Note the interaction with the legal guardrails: a signal (EDMW) URL found in a
published incident's `source_urls` is a guardrail #2 breach, and it is still NOT
auto-removed here, because removing it could take the incident's last source and
break guardrail #1. Report, alert, let a human re-source it.

## Detection 1 — double entries from the same source

Three shapes, in descending order of how much judgement the fix needs:
  same URL in >1 PUBLISHED incident   -> anomaly, human only
  same URL in >1 unprocessed queue row -> auto-dismissable duplicate
  a queued URL already published       -> auto-dismissable duplicate
plus near-duplicate titles from the same source inside a short window, which
needs a model to confirm — see the LLM budget note below.

## Detection 2 — hallucinating agents

Concrete, checkable signals only. This agent never asks a model whether another
model hallucinated: that question has no ground truth and the answer would be
one more generated claim. Every check here is a fact about the row or about the
world (does the URL resolve, is the date in the future, does the arithmetic add
up, is the domain in the operator's `sources` table).

URL liveness deliberately treats a network failure, a 403 and a 429 as UNKNOWN,
never as evidence of fabrication. Mothership and TOC sit behind Cloudflare and
refuse automated requests outright; flagging a real story as invented because a
publisher blocked us would be the exact failure this agent is supposed to catch.

## LLM budget

At most MAX_LLM_JUDGEMENTS Haiku calls per run (default 12), and usually zero.
Pairs reach the model only after: same source_name, dates inside a short window,
>= MIN_TITLE_OVERLAP shared keywords, and titles that are NOT already identical.
consolidation/check.py can afford MIN_KEYWORD_OVERLAP=1 because it compares one
candidate against a fixed pool; here the comparison is every pair in the pool,
so a loose pre-filter is quadratic in API spend rather than linear.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ops.activity import AgentRun, agent_enabled
from ops.notify import footer, notify, war_room_url

logger = logging.getLogger(__name__)

AGENT = "integrity"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# How far back to scan published incidents. The agent runs daily, so a 90-day
# window still sees every incident on ~90 separate passes. Widen it
# (INTEGRITY_WINDOW_DAYS=99999) for a one-off full-archive sweep.
PUBLISHED_WINDOW_DAYS = _int_env("INTEGRITY_WINDOW_DAYS", 90)
FETCH_LIMIT = _int_env("INTEGRITY_FETCH_LIMIT", 500)

# Near-duplicate pre-filter.
NEAR_DUP_WINDOW_DAYS = _int_env("INTEGRITY_NEAR_DUP_DAYS", 7)
MIN_TITLE_OVERLAP = _int_env("INTEGRITY_MIN_TITLE_OVERLAP", 3)
MAX_LLM_JUDGEMENTS = _int_env("INTEGRITY_MAX_JUDGEMENTS", 12)
# Pair-generation guard. Independent of the LLM cap: a pathological pool (one
# source_name on 400 rows) would otherwise burn the whole pass on comparisons.
MAX_PAIRS_CONSIDERED = _int_env("INTEGRITY_MAX_PAIRS", 4000)

# Network liveness cap. Each check is one HEAD; the cap is what stops a bad
# scrape day from turning this agent into a crawler.
MAX_URL_CHECKS = _int_env("INTEGRITY_MAX_URL_CHECKS", 40)
URL_TIMEOUT_SECONDS = float(os.getenv("INTEGRITY_URL_TIMEOUT", "6"))

# Yishun's HDB estate dates from the mid-1970s; nothing this archive covers can
# predate 1980. A date below it is a parse failure or a fabrication, not history.
EARLIEST_PLAUSIBLE = date(1980, 1, 1)

VALID_CLASSIFICATIONS = frozenset({"heart", "clown", "dagger", "custom"})

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_QUEUE_COLUMNS = (
    "id,created_at,source_url,source_type,status,processed_at,raw_content,"
    "proposed_title,proposed_summary,proposed_slug,proposed_classification,"
    "proposed_severity,agent_confidence,corroboration_count"
)
_INCIDENT_COLUMNS = (
    "id,created_at,published_at,incident_date,title,summary,slug,classification,"
    "severity,source_urls,corroboration_count,source_timeline"
)


# ── Finding ─────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """
    One integrity problem.

    `fix` is the whole safety model: None means "a human decides", and only the
    two ops named in the module docstring ever appear there. A detector cannot
    accidentally authorise a write by returning the wrong shape — run() only
    dispatches on ops it knows.
    """
    code: str                       # stable machine code, filterable in agent_events
    message: str
    level: str = "anomaly"          # 'warning' | 'anomaly'
    table: str = ""                 # 'incidents' | 'war_room_queue'
    row_id: str | None = None
    ref: str = ""                   # slug or title — what a human recognises
    fix: dict | None = None         # None => report only
    detail: dict = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.fix is None


# ── Parsing / normalisation ─────────────────────────────────────────────────

def _as_date(value) -> date | None:
    """Tolerant YYYY-MM-DD parse. Returns None rather than raising."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _day_precision_source_dates(source_timeline, source_urls) -> list[date]:
    """
    Publication dates of an incident's sources, at DAY precision only —
    source_timeline entry dates plus full `/YYYY/MM/DD/` (or `storyYYYYMMDD`)
    paths in the URLs. Month-only URL paths (`/2026/08/`) are deliberately
    excluded: read as the 1st they manufacture a false "after source" verdict
    (the exact bug that turned an 8-item audit into 51 false positives).
    """
    out: list[date] = []
    for e in (source_timeline or []):
        dd = _as_date((e or {}).get("date"))
        if dd:
            out.append(dd)
    for u in (source_urls or []):
        for m in re.finditer(r"/(\d{4})/(\d{2})/(\d{2})/", u or ""):
            try:
                out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        m = re.search(r"story(20\d{2})(\d{2})(\d{2})", u or "")
        if m:
            try:
                out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
    return out


def norm_url(url: str) -> str:
    """
    Grouping key for "is this the same source URL twice".

    Deliberately looser than ingestion/dedup.py, which matches EXACTLY. That
    exact match is why `.../story` and `.../story/` can both be sitting in the
    queue — a trailing slash, a `www.`, or an added tracking param walks straight
    past it. Those pairs ARE the double entries this agent exists to find, so the
    grouping key has to be looser than the check that let them through.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(raw)
        host = parts.netloc.lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return raw.lower().rstrip("/")
        path = parts.path.rstrip("/") or "/"
        return urlunsplit(("https", host, path, parts.query, ""))
    except Exception:                             # noqa: BLE001
        return raw.lower().rstrip("/")


def norm_title(title: str) -> str:
    """Case/punctuation-insensitive title key. Two rows agreeing here are the
    same headline, not merely a similar one."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _distinct(urls) -> list[str]:
    """Order-preserving distinct, blanks dropped."""
    return list(dict.fromkeys([u for u in (urls or []) if u]))


def _source_key(source_name: str, primary_url: str) -> str:
    """
    Machine-stable identity of "the same source", for grouping.

    The publisher domain, not the label: a queue row says "Channel NewsAsia"
    (raw_content.source_name) while a published incident has no source_name
    column at all, so grouping on the label alone would never match a draft
    against the live incident it duplicates. The label is kept for the message
    a human reads; the domain is what the grouping keys on.
    """
    from classifiers.source_allowlist import domain_of
    return domain_of(primary_url) or (source_name or "").strip().lower()


def queue_record(row: dict) -> dict:
    """
    war_room_queue row -> the common record shape the detectors work on.

    source_urls lives in raw_content (build_queue_row), not in a column, and
    older rows only have the single `source_url`. Falling back keeps the
    cardinality arithmetic honest for both generations of row.
    """
    rc = row.get("raw_content") if isinstance(row.get("raw_content"), dict) else {}
    urls = _distinct(rc.get("source_urls") or []) or _distinct([row.get("source_url")])
    return {
        "id":                  row.get("id"),
        "table":               "war_room_queue",
        "ref":                 (row.get("proposed_title") or rc.get("title") or "")[:100],
        "title":               row.get("proposed_title") or rc.get("title") or "",
        "summary":             row.get("proposed_summary") or rc.get("summary") or "",
        "source_name":         rc.get("source_name") or "",
        "source_key":          _source_key(rc.get("source_name") or "", row.get("source_url") or ""),
        "primary_url":         row.get("source_url") or "",
        "urls":                urls,
        "raw_urls":            list(rc.get("source_urls") or []),
        "date":                _as_date(rc.get("date") or rc.get("incident_date")),
        "created_at":          str(row.get("created_at") or ""),
        "slug":                row.get("proposed_slug") or rc.get("slug") or "",
        "classification":      row.get("proposed_classification"),
        "severity":            row.get("proposed_severity"),
        "corroboration_count": row.get("corroboration_count"),
        "is_notification":     bool(rc.get("notification_type")),
        "date_fallback":       bool(rc.get("_date_fallback")),
    }


def incident_record(row: dict) -> dict:
    """Published incidents row -> the same common record shape."""
    from classifiers.source_allowlist import domain_of
    urls = _distinct(row.get("source_urls") or [])
    return {
        "id":                  row.get("id"),
        "table":               "incidents",
        "ref":                 row.get("slug") or (row.get("title") or "")[:100],
        "title":               row.get("title") or "",
        "summary":             row.get("summary") or "",
        # Incidents carry no source_name column; the first source's domain is the
        # closest stand-in and is stable for the same-outlet grouping.
        "source_name":         domain_of(urls[0]) if urls else "",
        "source_key":          _source_key("", urls[0] if urls else ""),
        "primary_url":         urls[0] if urls else "",
        "urls":                urls,
        "raw_urls":            list(row.get("source_urls") or []),
        "source_timeline":     row.get("source_timeline") or [],
        "date":                _as_date(row.get("incident_date")),
        "created_at":          str(row.get("published_at") or row.get("created_at") or ""),
        "slug":                row.get("slug") or "",
        "classification":      row.get("classification"),
        "severity":            row.get("severity"),
        "corroboration_count": row.get("corroboration_count"),
        "is_notification":     False,
        "date_fallback":       False,
    }


# ── Detection 1a — exact duplicate source URLs ──────────────────────────────

def find_duplicate_urls(queue_records: list[dict],
                        incident_records: list[dict]) -> list[Finding]:
    """
    The literal requirement: "double entries from the same source".

    Grouped by normalised URL across BOTH tables, because the three collisions
    have three different remedies:

      >1 published incident  -> anomaly. Merging or deleting a live incident is
                                the operator's call; picking wrong here silently
                                rewrites the public archive.
      >1 queued row          -> keep the oldest, dismiss the rest. Deterministic,
                                nothing published, fully reversible.
      queued + published     -> dismiss the queue row(s): the story is already
                                live, so re-approving would publish a twin.
    """
    buckets: dict[str, dict[str, list[dict]]] = {}
    for rec in queue_records:
        # Sentinel rows (pattern alerts, lifecycle notices) are operator prompts,
        # not incidents. They can legitimately repeat a URL, and dismissing one
        # as a "duplicate" would delete a to-do the operator has not read yet.
        if rec.get("is_notification"):
            continue
        for url in rec["urls"] or ([rec["primary_url"]] if rec["primary_url"] else []):
            buckets.setdefault(norm_url(url), {"queue": [], "incidents": []})["queue"].append(rec)
    for rec in incident_records:
        for url in rec["urls"]:
            buckets.setdefault(norm_url(url), {"queue": [], "incidents": []})["incidents"].append(rec)

    findings: list[Finding] = []
    for url, group in buckets.items():
        if not url:
            continue
        # De-dupe within a bucket: one row listing the same URL twice is a
        # separate finding (see check_row_integrity), not a cross-row collision.
        queued = list({r["id"]: r for r in group["queue"]}.values())
        published = list({r["id"]: r for r in group["incidents"]}.values())

        if len(published) > 1:
            findings.append(Finding(
                code="dupe_url_published",
                level="anomaly",
                message=(f"{url} is cited by {len(published)} published incidents: "
                         + ", ".join(r["ref"] for r in published[:5])),
                table="incidents",
                row_id=published[0]["id"],
                ref=published[0]["ref"],
                detail={"url": url, "incident_ids": [r["id"] for r in published]},
            ))

        if published and queued:
            findings.append(Finding(
                code="queued_url_already_published",
                level="warning",
                message=(f"{len(queued)} queued row(s) cite {url}, already published as "
                         f"{published[0]['ref']}"),
                table="war_room_queue",
                row_id=queued[0]["id"],
                ref=queued[0]["ref"],
                fix={"op": "dismiss_queue_dupe",
                     "ids": [r["id"] for r in queued],
                     "keep": None,
                     "reason": f"source URL already published as {published[0]['ref']}"},
                detail={"url": url, "incident_id": published[0]["id"]},
            ))
        elif len(queued) > 1:
            ordered = sorted(queued, key=lambda r: r["created_at"] or "")
            keep, drop = ordered[0], ordered[1:]
            findings.append(Finding(
                code="dupe_url_queued",
                level="warning",
                message=(f"{len(queued)} unprocessed queue rows share {url}; "
                         f"keeping the oldest ({keep['ref']})"),
                table="war_room_queue",
                row_id=keep["id"],
                ref=keep["ref"],
                fix={"op": "dismiss_queue_dupe",
                     "ids": [r["id"] for r in drop],
                     "keep": keep["id"],
                     "reason": f"duplicate source URL of queue row {keep['id']}"},
                detail={"url": url, "queue_ids": [r["id"] for r in queued]},
            ))

    return findings


# ── Detection 1b — near-duplicate titles from the same source ───────────────

def _within_window(a: dict, b: dict, window_days: int) -> bool:
    """Dateless rows pair permissively — they are rare (a source must supply
    published_at to be registered) and the keyword bar still gates them."""
    if a["date"] is None or b["date"] is None:
        return True
    return abs((a["date"] - b["date"]).days) <= window_days


def find_near_duplicate_titles(records: list[dict],
                               window_days: int = NEAR_DUP_WINDOW_DAYS,
                               min_overlap: int = MIN_TITLE_OVERLAP,
                               max_pairs: int = MAX_PAIRS_CONSIDERED) -> dict:
    """
    Cheap pre-filter for "the same story from the same outlet, twice".

    Returns {"identical": [Finding], "ambiguous": [(a, b, overlap)],
             "pairs_considered": int, "pairs_capped": bool}.

    `identical` needs no model: same outlet, same normalised headline, dates
    inside the window. `ambiguous` is what a model has to settle, and run()
    spends its small LLM budget there.

    Grouping by source_name first is what makes this affordable — it turns one
    quadratic sweep over the pool into several tiny ones, and it matches the
    requirement, which is about double entries from the SAME source.
    """
    from consolidation.rules import extract_keywords, keyword_overlap

    groups: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("is_notification"):
            continue
        key = rec.get("source_key") or (rec.get("source_name") or "").strip().lower()
        if not key:
            continue
        groups.setdefault(key, []).append(rec)

    identical: list[Finding] = []
    ambiguous: list[tuple[dict, dict, int]] = []
    seen_identical: set[tuple] = set()
    considered = 0
    capped = False

    for source_name, pool in groups.items():
        if len(pool) < 2:
            continue
        keywords = {r["id"]: extract_keywords(f"{r['title']} {r['summary']}") for r in pool}
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                if considered >= max_pairs:
                    capped = True
                    break
                a, b = pool[i], pool[j]
                considered += 1
                if not _within_window(a, b, window_days):
                    continue

                if norm_title(a["title"]) and norm_title(a["title"]) == norm_title(b["title"]):
                    pair_key = tuple(sorted([str(a["id"]), str(b["id"])]))
                    if pair_key in seen_identical:
                        continue
                    seen_identical.add(pair_key)
                    identical.append(_identical_title_finding(a, b, source_name))
                    continue

                if keyword_overlap(keywords[a["id"]], keywords[b["id"]]) >= min_overlap:
                    ambiguous.append((a, b, keyword_overlap(keywords[a["id"]], keywords[b["id"]])))
            if capped:
                break
        if capped:
            break

    return {"identical": identical, "ambiguous": ambiguous,
            "pairs_considered": considered, "pairs_capped": capped}


def _identical_title_finding(a: dict, b: dict, source_name: str) -> Finding:
    """
    Same outlet, same headline, same window.

    Auto-dismissable ONLY when both sides are unprocessed queue rows AND the
    dates agree — that is the re-scrape signature (canonical URL vs Google News
    wrapper, QA M12), which is mechanical. Anything involving a published row,
    or dates that disagree, is a judgement about which entry is the real one and
    goes to the operator untouched.
    """
    both_queued = a["table"] == "war_room_queue" and b["table"] == "war_room_queue"
    same_date = a["date"] == b["date"]
    ordered = sorted([a, b], key=lambda r: r["created_at"] or "")
    keep, drop = ordered[0], ordered[1]

    fix = None
    if both_queued and same_date:
        fix = {"op": "dismiss_queue_dupe",
               "ids": [drop["id"]],
               "keep": keep["id"],
               "reason": f"identical headline from {source_name} as queue row {keep['id']}"}

    return Finding(
        code="dupe_title_identical" if both_queued else "dupe_title_published",
        level="warning" if fix else "anomaly",
        message=(f"identical headline from {source_name}: "
                 f"{a['table']}:{a['ref']} vs {b['table']}:{b['ref']} "
                 f"({a['date']} / {b['date']})"),
        table=keep["table"],
        row_id=keep["id"],
        ref=keep["ref"],
        fix=fix,
        detail={"a": a["id"], "b": b["id"], "source_name": source_name},
    )


def _confirmed_near_dup_finding(a: dict, b: dict, confidence: float, reason: str) -> Finding:
    """A model said these are one story. Never auto-applied — see module docstring."""
    return Finding(
        code="dupe_title_confirmed",
        level="anomaly",
        message=(f"same incident (conf={confidence:.2f}) from {a['source_name'] or '?'}: "
                 f"{a['table']}:{a['ref']} vs {b['table']}:{b['ref']} — {reason[:200]}"),
        table=a["table"],
        row_id=a["id"],
        ref=a["ref"],
        detail={"a": a["id"], "b": b["id"], "confidence": confidence},
    )


def _judge_near_duplicates(pairs: list[tuple[dict, dict, int]],
                           run: AgentRun, stats: dict) -> list[Finding]:
    """
    Spend the LLM budget on the ambiguous pairs, using consolidation's existing
    Haiku judge rather than a second prompt that could drift from it.

    A failure to build the client is not an error: the pass still reports every
    mechanical finding, it just cannot adjudicate the borderline pairs.
    """
    if not pairs:
        return []

    from consolidation.check import _get_anthropic_client, _judge_pair
    from consolidation.rules import UPDATE_MATCH_THRESHOLD

    try:
        claude = _get_anthropic_client()
    except Exception as exc:                      # noqa: BLE001
        run.warn("llm_unavailable",
                 f"{len(pairs)} ambiguous pair(s) left unjudged: {exc}")
        stats["llm_skipped"] = len(pairs)
        return []

    # Highest keyword overlap first: if the cap bites, it bites on the pairs
    # least likely to be duplicates.
    ordered = sorted(pairs, key=lambda p: -p[2])
    findings: list[Finding] = []

    for a, b, _overlap in ordered[:MAX_LLM_JUDGEMENTS]:
        try:
            verdict = _judge_pair(
                claude,
                {"title": a["title"], "summary": a["summary"],
                 "incident_date": str(a["date"] or ""), "url": a["primary_url"]},
                {"id": b["id"], "title": b["title"], "summary": b["summary"],
                 "incident_date": str(b["date"] or "")},
            )
        except Exception as exc:                  # noqa: BLE001
            run.warn("judge_failed", f"{a['ref']} vs {b['ref']}: {exc}")
            stats["errors"] += 1
            continue

        stats["llm_judgements"] += 1
        confidence = float(verdict.get("same_incident_confidence") or 0.0)
        if verdict.get("same_incident") and confidence >= UPDATE_MATCH_THRESHOLD:
            findings.append(_confirmed_near_dup_finding(
                a, b, confidence, verdict.get("same_incident_reason", "")))

    if len(ordered) > MAX_LLM_JUDGEMENTS:
        stats["llm_skipped"] = len(ordered) - MAX_LLM_JUDGEMENTS
        run.info("judgement_cap",
                 f"judged {MAX_LLM_JUDGEMENTS} of {len(ordered)} ambiguous pair(s); "
                 f"{stats['llm_skipped']} deferred to the next pass")
    return findings


# ── Detection 2 — hallucination signals ─────────────────────────────────────

def slug_date_conflict(slug: str, incident_date) -> str | None:
    """
    Return the expected `-mon-yyyy` suffix when the slug's stamped date
    contradicts incident_date, else None.

    Real bug: a 2026 incident shipped at a `-jul-2020` URL because the model was
    never told the date and guessed the year (see test_stage2_slug.py). The month
    table and suffix pattern are imported from the module that STAMPS the suffix,
    so a format change there cannot leave this checker quietly matching nothing.
    """
    d = _as_date(incident_date)
    if not slug or d is None:
        return None
    try:
        from filters.stage2_writer import _SLUG_DATE_SUFFIX, _SLUG_MONTHS
    except Exception as exc:                      # noqa: BLE001
        logger.debug("integrity: slug suffix rules unavailable (%s)", exc)
        return None

    match = _SLUG_DATE_SUFFIX.search(slug)
    if not match:
        return None                               # no stamped date: nothing to contradict
    stamped = [p for p in match.group(0).strip("-").split("-") if p]
    expected_month, expected_year = _SLUG_MONTHS[d.month - 1], str(d.year)

    if stamped[-1] != expected_year:
        return f"{expected_month}-{expected_year}"
    if len(stamped) > 1 and stamped[0] != expected_month:
        return f"{expected_month}-{expected_year}"
    return None


def check_row_integrity(rec: dict, today: date,
                        domains: dict | None = None) -> list[Finding]:
    """
    Every per-row hallucination signal that needs no network and no model.

    Pure: same record in, same findings out. That is what makes the whole
    detector testable against fixtures instead of against production.

    `domains` is the loaded `sources` allowlist; pass None (or {}) to skip the
    domain check entirely rather than flag every URL as unknown — a failed
    sources read must not manufacture 500 findings.
    """
    findings: list[Finding] = []
    published = rec["table"] == "incidents"
    level = "anomaly" if published else "warning"
    where = {"table": rec["table"], "row_id": rec["id"], "ref": rec["ref"]}

    if rec.get("is_notification"):
        return findings                           # sentinel rows are operator prompts, not incidents

    # ── guardrail #1: something must back this claim ────────────────────────
    if not rec["urls"]:
        findings.append(Finding(
            code="no_source_urls", level="anomaly",
            message="no source URL at all — nothing backs this write-up (guardrail #1)",
            **where))

    # ── source_urls listing the same URL twice ──────────────────────────────
    raw_urls = rec.get("raw_urls", rec["urls"])
    if len(raw_urls) != len({norm_url(u) for u in raw_urls if u}):
        findings.append(Finding(
            code="repeated_source_url", level=level,
            message=f"source_urls repeats a URL ({len(raw_urls)} entries, "
                    f"{len({norm_url(u) for u in raw_urls if u})} distinct)",
            detail={"source_urls": raw_urls}, **where))

    # ── dates that cannot be true ───────────────────────────────────────────
    d = rec["date"]
    if d is None:
        if not rec.get("date_fallback"):
            # A flagged fallback is already the operator's to-do (QA H3); an
            # unflagged missing date is a row that lost its date silently.
            findings.append(Finding(
                code="missing_incident_date", level=level,
                message="no incident date and no _date_fallback marker", **where))
    elif d > today:
        findings.append(Finding(
            code="future_incident_date", level="anomaly",
            message=f"incident_date {d} is in the future (today {today})",
            detail={"incident_date": str(d)}, **where))
    elif d < EARLIEST_PLAUSIBLE:
        findings.append(Finding(
            code="ancient_incident_date", level="anomaly",
            message=f"incident_date {d} predates {EARLIEST_PLAUSIBLE} — parse failure or fabrication",
            detail={"incident_date": str(d)}, **where))
    elif published:
        # A plausible past date — but it must not post-date its own coverage. An
        # event cannot be dated after the most recent article that reports it.
        # This is the standing-archive twin of stage2_writer._sanitise_event_date,
        # which runs only at ingestion and which the backfill path bypasses — the
        # two rows this first caught (a 2018 story dated 2026, a June story dated
        # September) were both backfill. Compared to the LATEST source, not the
        # earliest: a genuine opening legitimately post-dates a pre-event
        # announcement, but nothing post-dates its own most recent coverage.
        src = _day_precision_source_dates(rec.get("source_timeline"), rec.get("raw_urls"))
        latest = max(src) if src else None
        if latest and (d - latest).days > 1:
            findings.append(Finding(
                code="incident_date_after_source", level="anomaly",
                message=f"incident_date {d} post-dates its most recent source {latest} "
                        f"by {(d - latest).days}d — event dated after it was reported",
                detail={"incident_date": str(d), "latest_source": str(latest)}, **where))

    # ── QA H8: corroboration_count vs its own sources ───────────────────────
    # Expected is the DISTINCT count, matching build_queue_row: a URL repeated
    # in the array is not a second outlet corroborating the story, and the
    # lightning meter is derived from this number (bolts = count - 1).
    expected = max(1, len(rec["urls"]))
    actual = rec["corroboration_count"]
    if actual is not None and int(actual) != expected:
        findings.append(Finding(
            code="corroboration_drift", level="warning",
            message=f"corroboration_count {actual} != {expected} distinct source URL(s)",
            fix={"op": "corroboration_count", "table": rec["table"],
                 "id": rec["id"], "to": expected, "from": int(actual)},
            detail={"from": int(actual), "to": expected}, **where))

    # ── enum sanity ─────────────────────────────────────────────────────────
    # incidents has DB CHECKs for both; queue rows have neither, and a CHECK can
    # be dropped by a migration. Asserting is cheap, so assert on both tables.
    classification = rec.get("classification")
    if classification is not None and classification not in VALID_CLASSIFICATIONS:
        findings.append(Finding(
            code="bad_classification", level=level,
            message=f"classification {classification!r} is not one of {sorted(VALID_CLASSIFICATIONS)}",
            **where))

    severity = rec.get("severity")
    if severity is not None:
        try:
            if not 1 <= int(severity) <= 5:
                raise ValueError
        except (TypeError, ValueError):
            findings.append(Finding(
                code="bad_severity", level=level,
                message=f"severity {severity!r} outside 1-5", **where))

    # ── slug date contradicting incident_date ───────────────────────────────
    expected_suffix = slug_date_conflict(rec.get("slug") or "", d)
    if expected_suffix:
        findings.append(Finding(
            code="slug_date_conflict", level=level,
            message=f"slug {rec['slug']!r} contradicts incident_date {d} "
                    f"(expected …-{expected_suffix})",
            detail={"slug": rec["slug"], "expected_suffix": expected_suffix}, **where))

    # ── domains nobody approved / guardrail #2 breaches ─────────────────────
    if domains:
        from classifiers.source_allowlist import classify, domain_of
        for url in rec["urls"]:
            verdict = classify(url, domains)
            if verdict == "signal":
                findings.append(Finding(
                    code="signal_url_cited", level="anomaly",
                    message=f"guardrail #2 breach: signal source {domain_of(url)} is cited "
                            f"in source_urls — re-source by hand (removing it may take the "
                            f"last source and break guardrail #1)",
                    detail={"url": url}, **where))
            elif verdict == "unapproved":
                findings.append(Finding(
                    code="unknown_source_domain", level=level,
                    message=f"{domain_of(url)} is not an approved domain in the sources table",
                    detail={"url": url}, **where))

    return findings


# ── URL liveness ────────────────────────────────────────────────────────────

def url_status(url: str, timeout: float = URL_TIMEOUT_SECONDS) -> tuple[str, str]:
    """
    ('ok' | 'dead' | 'unknown', detail).

    HEAD only: this is a liveness probe, not a scrape, and the archive already
    has the article text. Only 404/410 — the server positively asserting the
    document does not exist — counts as dead. Everything else, including every
    network failure, is UNKNOWN. A flaky publisher must never get an incident
    flagged as fabricated; that mistake would be indistinguishable from the
    hallucination this check is looking for.
    """
    if not url:
        return "unknown", "empty url"
    try:
        import httpx
        resp = httpx.head(url, follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA})
        code = resp.status_code
        if code in (404, 410):
            return "dead", f"HTTP {code}"
        if code < 400:
            return "ok", f"HTTP {code}"
        # 405/501 = HEAD unsupported; 401/403/429 = bot detection (Mothership and
        # TOC 403 every automated request). None of these say "no such article".
        return "unknown", f"HTTP {code}"
    except Exception as exc:                      # noqa: BLE001
        return "unknown", f"{type(exc).__name__}: {str(exc)[:120]}"


def check_url_liveness(records: list[dict], run: AgentRun, stats: dict,
                       cap: int = MAX_URL_CHECKS) -> list[Finding]:
    """
    Probe up to `cap` distinct URLs, newest rows first — a fabricated URL is
    freshest on the row that just invented it, and the cap is logged so a partial
    sweep is never mistaken for a clean one.
    """
    by_url: dict[str, dict] = {}
    for rec in sorted(records, key=lambda r: r["created_at"] or "", reverse=True):
        for url in rec["urls"]:
            by_url.setdefault(url, rec)

    total = len(by_url)
    stats["urls_seen"] = total
    stats["url_check_cap"] = cap
    if total > cap:
        run.info("url_check_cap",
                 f"checking {cap} of {total} distinct source URL(s) this pass")

    findings: list[Finding] = []
    for url, rec in list(by_url.items())[:cap]:
        state, detail = url_status(url)
        stats["urls_checked"] += 1
        if state == "ok":
            continue
        if state == "unknown":
            stats["urls_unknown"] += 1
            run.info("url_unverified", f"{url} unreachable ({detail}) — NOT treated as fabricated",
                     source_name=rec.get("source_name") or None)
            continue
        stats["urls_dead"] += 1
        findings.append(Finding(
            code="dead_source_url",
            level="anomaly" if rec["table"] == "incidents" else "warning",
            message=f"source URL returns {detail}: {url}",
            table=rec["table"], row_id=rec["id"], ref=rec["ref"],
            detail={"url": url, "http": detail},
        ))
    return findings


# ── Corrections ─────────────────────────────────────────────────────────────

def _fix_corroboration_count(fix: dict, client, run: AgentRun, stats: dict) -> bool:
    try:
        client.table(fix["table"]).update(
            {"corroboration_count": fix["to"]}
        ).eq("id", fix["id"]).execute()
        run.success("corroboration_fixed",
                    f"{fix['table']}:{fix['id']} corroboration_count {fix['from']} -> {fix['to']}")
        return True
    except Exception as exc:                      # noqa: BLE001
        stats["errors"] += 1
        run.error_("corroboration_fix_failed", f"{fix['table']}:{fix['id']}: {exc}")
        return False


def _dismiss_queue_duplicates(fix: dict, queue_by_id: dict, client,
                              run: AgentRun, stats: dict) -> int:
    """
    Reject an unprocessed duplicate and record WHY as a training signal.

    The signal is the point: without it the learning loop sees a row vanish with
    no decision attached, and the ingestion side never learns that this
    source/shape produces double entries. decided_by='agent' keeps it out of the
    operator agreement-rate maths (migration 011) — the fleet must not be able to
    grade its own homework.
    """
    dismissed = 0
    for queue_id in fix.get("ids") or []:
        row = queue_by_id.get(queue_id) or {}
        if row.get("processed_at"):
            continue                              # a human got there first
        try:
            client.table("war_room_queue").update({
                "status": "rejected",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", queue_id).is_("processed_at", "null").execute()
        except Exception as exc:                  # noqa: BLE001
            stats["errors"] += 1
            run.error_("dismiss_failed", f"queue {queue_id}: {exc}")
            continue

        try:
            client.table("training_signals").insert({
                "queue_id":                queue_id,
                "action":                  "reject",
                "decision":                "reject",
                "reject_reason":           "duplicate",
                "decided_by":              "agent",
                "source_url":              row.get("source_url"),
                "source_type":             row.get("source_type"),
                "proposed_classification": row.get("proposed_classification"),
                "proposed_severity":       row.get("proposed_severity"),
                "original_draft":          row.get("proposed_summary"),
                "original_classification": row.get("proposed_classification"),
                "original_severity":       row.get("proposed_severity"),
                "agent_confidence":        row.get("agent_confidence"),
                "agent_confidence_was":    row.get("agent_confidence"),
                "operator_note":           f"Integrity agent: {fix.get('reason', 'duplicate')}",
            }).execute()
        except Exception as exc:                  # noqa: BLE001
            # Loud, not fatal: the row is already dismissed, but a silently
            # dropped signal is how the learning loop stops learning (cf. 009).
            stats["errors"] += 1
            run.error_("training_signal_failed", f"queue {queue_id}: {exc}")

        dismissed += 1
        run.success("queue_dupe_dismissed",
                    f"queue {queue_id} rejected as duplicate ({fix.get('reason', '')})")
    return dismissed


def apply_fixes(findings: list[Finding], queue_by_id: dict, client,
                run: AgentRun, stats: dict) -> None:
    """
    Dispatch only the two whitelisted ops. An unrecognised op is a bug, and it is
    treated as report-only rather than guessed at.

    `done` is load-bearing, not defensive noise: one queue row sharing two URLs
    with the same published incident produces two findings naming the same row,
    and dismissing it twice would write two `reject` training signals for one
    decision — silently doubling that row's weight in the learning loop.
    """
    done: set[tuple] = set()
    for finding in findings:
        fix = finding.fix
        if not fix:
            continue
        try:
            if fix["op"] == "corroboration_count":
                key = ("corroboration_count", fix["table"], fix["id"])
                if key in done:
                    continue
                done.add(key)
                if _fix_corroboration_count(fix, client, run, stats):
                    stats["corrected"] += 1
            elif fix["op"] == "dismiss_queue_dupe":
                pending = [i for i in (fix.get("ids") or []) if ("dismiss", i) not in done]
                if not pending:
                    continue
                done.update(("dismiss", i) for i in pending)
                fix = {**fix, "ids": pending}
                n = _dismiss_queue_duplicates(fix, queue_by_id, client, run, stats)
                stats["dismissed"] += n
                stats["corrected"] += n
            else:
                run.warn("unknown_fix_op", f"{fix['op']} not applied (unrecognised)")
        except Exception as exc:                  # noqa: BLE001
            stats["errors"] += 1
            run.error_("fix_failed", f"{finding.code}: {exc}")


# ── Operator alert ──────────────────────────────────────────────────────────

def _alert_body(needs_human: list[Finding], corrected: list[Finding],
                stats: dict, applied: bool) -> str:
    lines = [
        f"Integrity pass found {len(needs_human)} item(s) that need a human.",
        f"Scanned {stats['queue_rows']} queue row(s) and {stats['incidents']} published "
        f"incident(s) from the last {PUBLISHED_WINDOW_DAYS} days.",
        "",
    ]

    dupes = [f for f in needs_human if f.code.startswith("dupe_") or f.code.startswith("queued_")]
    others = [f for f in needs_human if f not in dupes]

    if dupes:
        lines.append(f"DOUBLE ENTRIES ({len(dupes)}):")
        lines += [f"  [{f.code}] {f.message}" for f in dupes[:25]]
        if len(dupes) > 25:
            lines.append(f"  ... and {len(dupes) - 25} more")
        lines.append("")

    if others:
        lines.append(f"DATA-INTEGRITY SIGNALS ({len(others)}):")
        lines += [f"  [{f.code}] {f.ref}: {f.message}" for f in others[:25]]
        if len(others) > 25:
            lines.append(f"  ... and {len(others) - 25} more")
        lines.append("")

    if corrected:
        verb = "Auto-corrected" if applied else "Correctable automatically (report-only pass)"
        lines.append(f"{verb} ({len(corrected)}):")
        lines += [f"  [{f.code}] {f.ref}: {f.message}" for f in corrected[:15]]
        if len(corrected) > 15:
            lines.append(f"  ... and {len(corrected) - 15} more")
        lines.append("")

    if not applied:
        lines += [
            "This pass was REPORT-ONLY (apply=False). Nothing was written.",
            "Published incidents are never edited by this agent under any setting.",
            "",
        ]

    if stats.get("urls_unknown"):
        lines.append(
            f"{stats['urls_unknown']} source URL(s) were unreachable and are NOT reported "
            f"as fabricated — a blocked or flaky publisher is not evidence of anything."
        )
    if stats.get("llm_skipped"):
        lines.append(
            f"{stats['llm_skipped']} ambiguous pair(s) exceeded the per-run judgement "
            f"budget and roll over to the next pass."
        )

    lines.append(f"\nReview: {war_room_url('/queue')}")
    return "\n".join(lines) + footer()


# ── Entry point ─────────────────────────────────────────────────────────────

def run(supabase_client=None, apply: bool = False, trigger: str = "scheduler") -> dict:
    """
    One integrity pass. Never raises — returns a stats dict with `errors`.

    apply=False (the default) reports and alerts without writing anything. See
    the module docstring for why that asymmetry is deliberate.
    """
    stats = {
        "apply": apply, "queue_rows": 0, "incidents": 0,
        "findings": 0, "duplicates": 0, "integrity_signals": 0, "needs_human": 0,
        "corrected": 0, "dismissed": 0,
        "llm_judgements": 0, "llm_skipped": 0, "pairs_considered": 0,
        "urls_checked": 0, "urls_dead": 0, "urls_unknown": 0,
        "errors": 0,
    }

    if not agent_enabled(AGENT):
        logger.warning("integrity: disabled via AGENT_DISABLED")
        stats["disabled"] = True
        return stats

    try:
        # An injected client is used for the activity log too, not just the scan:
        # one run must not end up writing its findings and its own audit trail to
        # two different databases (and it keeps the tests off the network).
        with AgentRun(AGENT, trigger=trigger, client=supabase_client) as arun:
            try:
                _pass(supabase_client, apply, arun, stats)
            except Exception as exc:              # noqa: BLE001
                stats["errors"] += 1
                arun.error_("integrity_crashed", f"{type(exc).__name__}: {exc}")
                arun.fail(f"{type(exc).__name__}: {exc}")
    except Exception as exc:                      # noqa: BLE001
        # AgentRun swallows its own failures, so reaching here means something
        # broke in the logging layer itself. The caller still gets its dict.
        stats["errors"] += 1
        logger.exception("integrity: run wrapper failed: %s", exc)

    return stats


def _pass(supabase_client, apply: bool, arun: AgentRun, stats: dict) -> None:
    """The actual work. Anything raised here is caught by run()."""
    from classifiers.corroboration import get_supabase_client
    from classifiers.source_allowlist import load_source_domains

    client = supabase_client if supabase_client is not None else get_supabase_client()
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=PUBLISHED_WINDOW_DAYS)).isoformat()

    arun.stat("window_days", PUBLISHED_WINDOW_DAYS)
    arun.stat("apply", apply)

    # ── Fetch ───────────────────────────────────────────────────────────────
    # Drafts are RLS-invisible to the publishable key; this agent runs on the
    # secret key via get_supabase_client(), so the queue is actually readable.
    queue_rows: list[dict] = []
    try:
        res = (client.table("war_room_queue").select(_QUEUE_COLUMNS)
               .is_("processed_at", "null")
               .order("created_at", desc=True).limit(FETCH_LIMIT).execute())
        queue_rows = res.data or []
    except Exception as exc:                      # noqa: BLE001
        stats["errors"] += 1
        arun.error_("queue_fetch_failed", str(exc))

    incident_rows: list[dict] = []
    try:
        res = (client.table("incidents").select(_INCIDENT_COLUMNS)
               .eq("is_published", True)
               .gte("published_at", cutoff)
               .order("published_at", desc=True).limit(FETCH_LIMIT).execute())
        incident_rows = res.data or []
    except Exception as exc:                      # noqa: BLE001
        stats["errors"] += 1
        arun.error_("incident_fetch_failed", str(exc))

    queue_records = [queue_record(r) for r in queue_rows]
    incident_records = [incident_record(r) for r in incident_rows]
    all_records = queue_records + incident_records
    queue_by_id = {r["id"]: r for r in queue_rows}

    stats["queue_rows"] = len(queue_records)
    stats["incidents"] = len(incident_records)
    arun.stat("queue_rows", stats["queue_rows"])
    arun.stat("incidents_scanned", stats["incidents"])

    if not all_records:
        arun.set_summary("nothing to scan")
        return

    # ── Detection 1 — duplicates ────────────────────────────────────────────
    findings: list[Finding] = find_duplicate_urls(queue_records, incident_records)

    near = find_near_duplicate_titles(all_records)
    stats["pairs_considered"] = near["pairs_considered"]
    arun.stat("pairs_considered", near["pairs_considered"])
    if near["pairs_capped"]:
        arun.warn("pair_cap_hit",
                  f"stopped after {MAX_PAIRS_CONSIDERED} title comparisons — "
                  f"pool too large for one pass")
    findings += near["identical"]
    arun.stat("pairs_to_judge", len(near["ambiguous"]))
    findings += _judge_near_duplicates(near["ambiguous"], arun, stats)
    stats["duplicates"] = len(findings)

    # ── Detection 2 — hallucination signals ─────────────────────────────────
    try:
        domains = load_source_domains(client)
        if not domains:
            arun.warn("allowlist_unavailable",
                      "sources table unreadable — domain checks skipped this pass")
    except Exception as exc:                      # noqa: BLE001
        stats["errors"] += 1
        arun.warn("allowlist_unavailable", f"domain checks skipped: {exc}")
        domains = {}

    integrity_findings: list[Finding] = []
    for rec in all_records:
        try:
            integrity_findings += check_row_integrity(rec, today, domains)
        except Exception as exc:                  # noqa: BLE001
            stats["errors"] += 1
            arun.error_("row_check_failed", f"{rec['table']}:{rec['id']}: {exc}")

    integrity_findings += check_url_liveness(all_records, arun, stats)
    stats["integrity_signals"] = len(integrity_findings)
    findings += integrity_findings

    # ── Report ──────────────────────────────────────────────────────────────
    stats["findings"] = len(findings)
    for finding in findings:
        emit = arun.anomaly if finding.level == "anomaly" else arun.warn
        emit(finding.code, f"{finding.table}:{finding.ref}: {finding.message}",
             row_id=finding.row_id, fixable=bool(finding.fix), **finding.detail)

    fixable = [f for f in findings if f.fix]
    needs_human = [f for f in findings if f.needs_human]
    stats["needs_human"] = len(needs_human)
    arun.stat("findings", len(findings))
    arun.stat("needs_human", len(needs_human))
    arun.stat("auto_fixable", len(fixable))

    # ── Correct ─────────────────────────────────────────────────────────────
    if apply and fixable:
        apply_fixes(fixable, queue_by_id, client, arun, stats)
    elif fixable:
        arun.info("report_only",
                  f"{len(fixable)} auto-correctable finding(s) left untouched (apply=False)")

    arun.stat("corrected", stats["corrected"])
    arun.set_summary(
        f"{stats['findings']} finding(s): {stats['duplicates']} duplicate-shaped, "
        f"{stats['integrity_signals']} integrity; {stats['corrected']} corrected, "
        f"{len(needs_human)} for the operator "
        f"({'apply' if apply else 'report-only'}, {stats['llm_judgements']} LLM call(s))"
    )

    # ── Notify — only when a human is actually needed ───────────────────────
    if needs_human:
        notify(
            "anomaly",
            f"Yishun Again — integrity: {len(needs_human)} item(s) need review",
            _alert_body(needs_human, fixable, stats, apply),
            # Keyed on the calendar day: the same duplicate found on every pass
            # must not mail every pass, or the operator filters the sender and
            # the one alert that mattered goes unread too.
            dedup_key=f"integrity:{today.isoformat()}",
            client=client,
        )
        stats["notified"] = True
