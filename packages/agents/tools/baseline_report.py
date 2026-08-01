"""
Baseline extraction (cost + classification programme, phase 0b).

READ-ONLY. Queries Supabase with SUPABASE_SECRET_KEY and prints five sections:

  1. agent_runs      — count + max(started_at) per agent, last 14 days
  2. agent_events    — every clustering-related event, verbatim, with timestamps
  3. incidents       — published in the last 60 days: total, single-source count, pct
  4. war_room_queue  — row count by status
  5. incidents       — published total, and how many carry a pixel_art_url

Section 3 is the programme's success measure. Cost falling while the
single-source percentage RISES means money was saved by breaking clustering.

No files written, no rows modified. Run:

    ./.venv/Scripts/python.exe tools/baseline_report.py
"""

import pathlib
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

_AGENTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENTS_ROOT.parents[1]
sys.path.insert(0, str(_AGENTS_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env", override=False)

from classifiers.corroboration import get_supabase_client  # noqa: E402

RUNS_WINDOW_DAYS = 14
INCIDENTS_WINDOW_DAYS = 60
PAGE = 1000


def _rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _page_all(client, table: str, columns: str, apply_filters=None) -> list[dict]:
    """
    Fetch every row matching the filters, paging past PostgREST's 1000-row cap.

    A silently truncated read would understate exactly the counts this report
    exists to establish, so paging is not optional here.
    """
    rows: list[dict] = []
    offset = 0
    while True:
        q = client.table(table).select(columns)
        if apply_filters is not None:
            q = apply_filters(q)
        res = q.range(offset, offset + PAGE - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        offset += PAGE


# ── 1. agent_runs ────────────────────────────────────────────────────────────

def section_agent_runs(client) -> None:
    _rule(f"1. agent_runs — last {RUNS_WINDOW_DAYS} days")
    since = _iso_days_ago(RUNS_WINDOW_DAYS)
    try:
        rows = _page_all(
            client, "agent_runs", "agent,started_at,status",
            lambda q: q.gte("started_at", since).order("started_at", desc=True),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  QUERY FAILED: {exc}")
        return

    if not rows:
        print(f"  No agent_runs rows since {since}.")
        return

    counts: Counter = Counter()
    latest: dict[str, str] = {}
    statuses: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        agent = r.get("agent") or "(null)"
        counts[agent] += 1
        started = r.get("started_at") or ""
        if started > latest.get(agent, ""):
            latest[agent] = started
        statuses[agent][r.get("status") or "(null)"] += 1

    print(f"  {'agent':<22} {'runs':>5}  {'max(started_at)':<30} status breakdown")
    print(f"  {'-' * 22} {'-' * 5}  {'-' * 30} {'-' * 24}")
    for agent, n in counts.most_common():
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(statuses[agent].items()))
        print(f"  {agent:<22} {n:>5}  {latest[agent]:<30} {breakdown}")
    print(f"\n  TOTAL runs in window: {len(rows)} across {len(counts)} agent(s)")
    # Expected shape is ~8 rows/day: daily_orchestrator plus seven named agents.
    # Ingestion is deliberately absent — daily.py hands it the orchestrator's own
    # run (activity=arun) instead of opening a second one, so a missing
    # agent='ingestion' row is the design, not a dead agent.
    print("  NOTE: no agent='ingestion' row is expected — ingestion runs inside")
    print("        daily_orchestrator's run (daily.py passes activity=arun).")


# ── 2. agent_events (clustering) ─────────────────────────────────────────────

def section_clustering_events(client) -> None:
    _rule("2. agent_events — clustering references (verbatim)")
    # Column is `event`, not `event_type` (see ops/activity.py::AgentRun.event).
    try:
        rows = _page_all(
            client, "agent_events",
            "created_at,agent,level,event,message,source_name,detail",
            lambda q: q.order("created_at", desc=True),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  QUERY FAILED: {exc}")
        return

    needles = ("cluster", "shadow-cluster", "cluster_oversized")

    def hits(row: dict) -> bool:
        blob = f"{row.get('event') or ''} {row.get('message') or ''}".lower()
        return any(n in blob for n in needles)

    matched = [r for r in rows if hits(r)]
    print(f"  Scanned {len(rows)} agent_events row(s); {len(matched)} reference clustering.\n")
    if not matched:
        print("  (none — clustering has never logged to agent_events)")
        return

    by_event: Counter = Counter(r.get("event") or "(null)" for r in matched)
    print("  By event name:")
    for ev, n in by_event.most_common():
        print(f"    {ev:<32} {n}")
    print()

    for r in matched:
        print(f"  [{r.get('created_at')}] {r.get('agent')} / {r.get('level')} / {r.get('event')}")
        print(f"      {r.get('message')}")
        if r.get("source_name"):
            print(f"      source_name={r.get('source_name')}")
        if r.get("detail"):
            print(f"      detail={r.get('detail')}")
        print()


# ── 3. published incidents: single-source rate ───────────────────────────────

def section_single_source(client) -> None:
    _rule(f"3. Published incidents — last {INCIDENTS_WINDOW_DAYS} days (SUCCESS METRIC)")
    since = _iso_days_ago(INCIDENTS_WINDOW_DAYS)
    try:
        rows = _page_all(
            client, "incidents",
            "id,slug,source_urls,published_at,incident_date,corroboration_count",
            lambda q: q.eq("is_published", True).gte("published_at", since)
                       .order("published_at", desc=True),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  QUERY FAILED: {exc}")
        return

    total = len(rows)
    if total == 0:
        print(f"  No incidents published since {since}.")
        return

    def n_sources(row: dict) -> int:
        urls = row.get("source_urls") or []
        return len(urls) if isinstance(urls, list) else 0

    single = sum(1 for r in rows if n_sources(r) == 1)
    zero = sum(1 for r in rows if n_sources(r) == 0)
    pct = 100.0 * single / total

    print(f"  Published in window          : {total}")
    print(f"  cardinality(source_urls) = 1 : {single}")
    print(f"  SINGLE-SOURCE PERCENTAGE     : {pct:.1f}%")
    if zero:
        print(f"  !! cardinality(source_urls) = 0 : {zero}  <-- GUARDRAIL #1 BREACH")

    dist: Counter = Counter(n_sources(r) for r in rows)
    print("\n  Source-count distribution:")
    for k in sorted(dist):
        bar = "#" * min(60, dist[k])
        print(f"    {k:>2} source(s): {dist[k]:>4}  {bar}")

    corr: Counter = Counter(r.get("corroboration_count") for r in rows)
    print("\n  corroboration_count distribution:")
    for k in sorted(corr, key=lambda v: (v is None, v)):
        print(f"    {str(k):>4}: {corr[k]}")


# ── 4. war_room_queue by status ──────────────────────────────────────────────

def section_queue(client) -> None:
    _rule("4. war_room_queue — rows by status")
    try:
        rows = _page_all(client, "war_room_queue", "id,status,processed_at,created_at")
    except Exception as exc:  # noqa: BLE001
        print(f"  QUERY FAILED: {exc}")
        return

    if not rows:
        print("  war_room_queue is empty.")
        return

    by_status: Counter = Counter(r.get("status") or "(null)" for r in rows)
    print(f"  {'status':<24} {'rows':>6}")
    print(f"  {'-' * 24} {'-' * 6}")
    for status, n in by_status.most_common():
        print(f"  {status:<24} {n:>6}")
    print(f"  {'-' * 24} {'-' * 6}")
    print(f"  {'TOTAL':<24} {len(rows):>6}")

    unprocessed = sum(1 for r in rows if r.get("processed_at") is None)
    print(f"\n  processed_at IS NULL (awaiting operator): {unprocessed}")


# ── 5. pixel art coverage (all published incidents) ──────────────────────────

def section_pixel_art(client) -> None:
    _rule("5. Published incidents — pixel_art_url coverage (all time)")
    try:
        rows = _page_all(
            client, "incidents", "id,pixel_art_url",
            lambda q: q.eq("is_published", True),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  QUERY FAILED: {exc}")
        return

    total = len(rows)
    if total == 0:
        print("  No published incidents.")
        return

    def has_art(row: dict) -> bool:
        url = row.get("pixel_art_url")
        return isinstance(url, str) and url.strip() != ""

    with_art = sum(1 for r in rows if has_art(r))
    print(f"  Published incidents total    : {total}")
    print(f"  pixel_art_url IS NOT NULL    : {with_art}")
    print(f"  COVERAGE                     : {100.0 * with_art / total:.1f}%")


def main() -> int:
    print("Yishun Again — baseline report (read-only)")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    try:
        client = get_supabase_client()
    except Exception as exc:  # noqa: BLE001
        print(f"\nFATAL: could not build Supabase client: {exc}")
        return 1

    section_agent_runs(client)
    section_clustering_events(client)
    section_single_source(client)
    section_queue(client)
    section_pixel_art(client)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
