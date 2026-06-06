"""
Orchestrator — LangGraph pipeline (spec §4, Step 9).

Graph (per item):
  content_intake
      |-- [duplicate] --> END
      |-- [new]       --> stage1_filter
                              |-- [reject/error] --> END
                              |-- [pass]         --> stage2_writer
                                                          |
                                                    corroboration
                                                          |
                                                    queue_insert
                                                          |
                                                    herald_check --> END

Public API:
  run_graph(items: list[dict], dry_run: bool = False) -> dict
"""

import logging
import operator
import time
from typing import Annotated, Any, Optional

from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

_INTER_ITEM_DELAY = 1.5   # seconds between Stage 2 calls (Anthropic rate limit)


# ── Graph state ───────────────────────────────────────────────────────────────

class GraphState(TypedDict):
    # Set before invoke — shared across all nodes
    item:     dict   # current scraped item (enriched by content_intake)
    dry_run:  bool
    supabase: Any    # admin Supabase client, or None

    # Per-node outputs
    s1_result:     Optional[dict]   # stage1_filter output
    s2_draft:      Optional[dict]   # stage2_writer output (merged by corroboration)
    queue_id:      Optional[str]    # war_room_queue row ID after insert
    herald_result: Optional[dict]   # herald_check output

    # Control flow
    skipped:     bool
    skip_reason: Optional[str]   # "duplicate" | "stage2_error"
    error:       Optional[str]

    # Execution trace — accumulated across all nodes via operator.add
    node_trace: Annotated[list[str], operator.add]


# ── Nodes ─────────────────────────────────────────────────────────────────────

def content_intake(state: GraphState) -> dict:
    """
    Validate and normalise a scraped item before it enters the filter chain.
    - Checks for duplicates against war_room_queue + incidents.
    - Sets source_urls / edmw_signal_count per EDMW signal rules (spec §4).
    """
    from classifiers.corroboration import check_duplicate

    item        = state["item"]
    url         = item.get("url", "")
    source_type = item.get("source_type", "msm")
    supabase    = state.get("supabase")

    trace = [f"content_intake [{item.get('source_name', '?')}] {url[:70]}"]

    if check_duplicate(url, client=supabase):
        return {
            "skipped":     True,
            "skip_reason": "duplicate",
            "node_trace":  trace + ["  -> duplicate — END"],
        }

    # EDMW signals are never added to source_urls (spec §13)
    if source_type == "signal":
        source_urls       = []
        edmw_signal_count = 1
    else:
        source_urls       = [url] if url else []
        edmw_signal_count = item.get("edmw_signal_count", 0)

    return {
        "item":      {**item, "source_urls": source_urls, "edmw_signal_count": edmw_signal_count},
        "node_trace": trace,
    }


def stage1_filter(state: GraphState) -> dict:
    """
    Groq Stage 1 noise filter (llama-3.1-8b-instant).
    Pass threshold: confidence >= 0.4.
    """
    from filters.stage1_filter import filter_content

    item  = state["item"]
    trace = [f"stage1_filter [{item.get('source_name', '?')}]"]

    try:
        result  = filter_content(item)
        verdict = "PASS" if result["passes"] else "REJECT"
        trace.append(
            f"  -> {verdict} conf={result['confidence']:.2f}"
            f" | {result.get('reason', '')[:80]}"
        )
        return {"s1_result": result, "node_trace": trace}
    except Exception as exc:
        err = str(exc)
        logger.error("stage1_filter error [%s]: %s", item.get("url", "?"), err)
        failed = {"passes": False, "confidence": 0.0, "reason": err}
        return {"s1_result": failed, "error": err, "node_trace": trace + [f"  -> ERROR: {err}"]}


def stage2_writer(state: GraphState) -> dict:
    """
    Claude Stage 2: Haiku classification + Sonnet draft writing.
    Returns a complete draft dict for the war_room_queue row.
    """
    from filters.stage2_writer import write_stage2

    item  = state["item"]
    trace = [f"stage2_writer [{item.get('source_name', '?')}]"]

    try:
        draft = write_stage2(item)
        trace.append(
            f"  -> [{draft['classification'].upper()}]"
            f" sev={draft['severity']}"
            f" conf={draft['confidence']:.2f}"
            f" hype={draft.get('hype_meter', 0)}"
            f" deaths={draft.get('deaths')!r}"
        )
        return {"s2_draft": draft, "node_trace": trace}
    except Exception as exc:
        err = str(exc)
        logger.error("stage2_writer error [%s]: %s", item.get("url", "?"), err)
        return {
            "error":       err,
            "skipped":     True,
            "skip_reason": "stage2_error",
            "node_trace":  trace + [f"  -> ERROR: {err}"],
        }


def corroboration(state: GraphState) -> dict:
    """
    Re-compute hype_meter from authoritative source_urls.
    Sets corroboration_count = 1 (Phase 1; cross-source matching is future work).
    """
    from classifiers.corroboration import compute_hype_meter

    draft = state.get("s2_draft")
    if not draft:
        return {"node_trace": ["corroboration: no draft — skipping"]}

    source_urls = state["item"].get("source_urls", [])
    hype        = compute_hype_meter(source_urls)

    return {
        "s2_draft":  {**draft, "hype_meter": hype, "corroboration_count": 1},
        "node_trace": [f"corroboration: hype_meter={hype} corroboration_count=1"],
    }


def queue_insert(state: GraphState) -> dict:
    """
    Insert the approved draft into war_room_queue.
    No-op in dry_run mode or when s2_draft is absent.
    """
    draft    = state.get("s2_draft")
    item     = state["item"]
    supabase = state.get("supabase")
    dry_run  = state.get("dry_run", False)

    if not draft:
        return {"node_trace": ["queue_insert: no draft — skipping"]}

    edmw_signal_count = item.get("edmw_signal_count", 0)
    queue_row = {
        "raw_content":             {**item, **draft},
        "source_url":              item.get("url", ""),
        "source_type":             item.get("source_type", "msm"),
        "proposed_title":          draft["title"],
        "proposed_summary":        draft["summary"],
        "proposed_classification": draft["classification"],
        "proposed_severity":       draft["severity"],
        "proposed_pixel_prompt":   draft.get("pixel_art_prompt", ""),
        "proposed_slug":           draft.get("slug", ""),
        "agent_confidence":        draft["confidence"],
        "corroboration_count":     draft.get("corroboration_count", 1),
        "edmw_signal_count":       edmw_signal_count,
        "status":                  "pending",
    }

    if dry_run:
        msg = (
            f"queue_insert: DRY RUN"
            f" [{draft['classification'].upper()}]"
            f" sev={draft['severity']}"
            f" hype={draft.get('hype_meter', 0)}"
            f" | {draft['title'][:60]}"
        )
        return {"node_trace": [msg]}

    if not supabase:
        return {"node_trace": ["queue_insert: no Supabase client — skipping"]}

    try:
        res    = supabase.table("war_room_queue").insert(queue_row).select("id").execute()
        new_id = (res.data or [{}])[0].get("id")
        return {
            "queue_id":  new_id,
            "node_trace": [f"queue_insert: inserted id={new_id}"],
        }
    except Exception as exc:
        err = str(exc)
        logger.error("queue_insert error: %s", err)
        return {"error": err, "node_trace": [f"queue_insert: ERROR: {err}"]}


def herald_check(state: GraphState) -> dict:
    """
    Run the herald agent to check milestone thresholds after a queue insert.
    Always non-fatal — a herald failure must never block the pipeline.
    """
    from orchestrator.herald_agent import check_milestones

    draft    = state.get("s2_draft")
    queue_id = state.get("queue_id")
    supabase = state.get("supabase")
    item     = state["item"]
    dry_run  = state.get("dry_run", False)

    if dry_run or not queue_id or not supabase:
        reason = "dry_run" if dry_run else ("no queue_id" if not queue_id else "no supabase")
        return {"node_trace": [f"herald_check: skipped ({reason})"]}

    try:
        result    = check_milestones(
            draft           = draft or {},
            queue_id        = queue_id,
            source_url      = item.get("url", ""),
            incident_title  = (draft or {}).get("title", ""),
            supabase_client = supabase,
        )
        triggered = result.get("triggered", [])
        msg = (
            f"herald_check: triggered=[{', '.join(triggered)}]"
            if triggered else "herald_check: no milestones"
        )
        return {"herald_result": result, "node_trace": [msg]}
    except Exception as exc:
        logger.warning("Herald error (non-fatal): %s", exc)
        return {"node_trace": [f"herald_check: error (non-fatal): {exc}"]}


# ── Routing functions ─────────────────────────────────────────────────────────

def _route_intake(state: GraphState) -> str:
    return END if state.get("skipped") else "stage1_filter"


def _route_s1(state: GraphState) -> str:
    s1 = state.get("s1_result") or {}
    return "stage2_writer" if s1.get("passes") else END


# ── Graph compilation ─────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(GraphState)

    g.add_node("content_intake", content_intake)
    g.add_node("stage1_filter",  stage1_filter)
    g.add_node("stage2_writer",  stage2_writer)
    g.add_node("corroboration",  corroboration)
    g.add_node("queue_insert",   queue_insert)
    g.add_node("herald_check",   herald_check)

    g.set_entry_point("content_intake")

    g.add_conditional_edges("content_intake", _route_intake)
    g.add_conditional_edges("stage1_filter",  _route_s1)

    g.add_edge("stage2_writer", "corroboration")
    g.add_edge("corroboration", "queue_insert")
    g.add_edge("queue_insert",  "herald_check")
    g.add_edge("herald_check",  END)

    return g


_app = _build_graph().compile()


# ── Public API ────────────────────────────────────────────────────────────────

def run_graph(items: list[dict], dry_run: bool = False) -> dict:
    """
    Process a list of scraped items through the LangGraph pipeline.

    Each item traverses the graph independently:
      content_intake -> stage1_filter -> stage2_writer -> corroboration
                                                              -> queue_insert -> herald_check

    Args:
        items:   Scraped content dicts from scrape_all().
        dry_run: If True, AI calls run normally but nothing is written to Supabase.

    Returns:
        Aggregated stats dict.
    """
    from classifiers.corroboration import get_supabase_client

    supabase = None
    if not dry_run:
        try:
            supabase = get_supabase_client()
        except EnvironmentError as exc:
            logger.error("Supabase not configured — cannot run live graph: %s", exc)
            return {
                "total": len(items), "s1_passed": 0, "s1_rejected": 0,
                "duplicates": 0, "s2_errors": 0, "queued": 0, "errors": 1,
                "herald_triggered": [], "dry_run": False,
            }

    stats: dict = {
        "total":            len(items),
        "s1_passed":        0,
        "s1_rejected":      0,
        "duplicates":       0,
        "s2_errors":        0,
        "queued":           0,
        "errors":           0,
        "herald_triggered": [],
        "dry_run":          dry_run,
    }

    for idx, item in enumerate(items, 1):
        logger.info("Graph [%d/%d] %s", idx, len(items), item.get("url", "?")[:70])

        initial: GraphState = {
            "item":          item,
            "dry_run":       dry_run,
            "supabase":      supabase,
            "s1_result":     None,
            "s2_draft":      None,
            "queue_id":      None,
            "herald_result": None,
            "skipped":       False,
            "skip_reason":   None,
            "error":         None,
            "node_trace":    [],
        }

        try:
            final = _app.invoke(initial)
        except Exception as exc:
            logger.error("Graph invoke failed [%s]: %s", item.get("url", "?"), exc)
            stats["errors"] += 1
            continue

        # Accumulate stats
        skip   = final.get("skip_reason")
        s1     = final.get("s1_result") or {}

        if skip == "duplicate":
            stats["duplicates"] += 1
        elif skip == "stage2_error":
            stats["s2_errors"] += 1
        elif s1.get("passes"):
            stats["s1_passed"] += 1
        elif s1:
            stats["s1_rejected"] += 1

        if final.get("queue_id"):
            stats["queued"] += 1

        if final.get("error") and skip not in ("duplicate", "stage2_error"):
            stats["errors"] += 1

        for t in final.get("herald_result", {}).get("triggered", []):
            stats["herald_triggered"].append(t)

        for step in final.get("node_trace", []):
            logger.debug("  %s", step)

        time.sleep(_INTER_ITEM_DELAY)

    logger.info(
        "Graph done — total=%d s1_pass=%d s1_rej=%d dupes=%d s2_err=%d queued=%d err=%d",
        stats["total"],      stats["s1_passed"],   stats["s1_rejected"],
        stats["duplicates"], stats["s2_errors"],   stats["queued"], stats["errors"],
    )
    return stats


# ── Offline test ──────────────────────────────────────────────────────────────
# Run: python packages/agents/orchestrator/orchestrator.py
# Mocks AI calls and Supabase — no real API keys required.

def _run_test() -> None:
    from unittest.mock import MagicMock, patch

    print("\n" + "=" * 64)
    print("Orchestrator -- LangGraph offline test (3 items)")
    print("=" * 64)

    # ── Mock S1 results (item 3 never reaches S1 — caught as duplicate) ────
    s1_results = [
        {"passes": True,  "confidence": 0.87, "reason": "Specific Yishun stabbing incident — relevant"},
        {"passes": False, "confidence": 0.18, "reason": "Generic Singapore policy news — no Yishun incident"},
    ]

    s2_mock = {
        "title":             "Man stabbed 11 times in Yishun flat over noise dispute",
        "summary":           (
            "A 45-year-old man was charged Thursday after allegedly stabbing his neighbour "
            "11 times with a kitchen knife at Block 873 Yishun Ring Road, following a months-long "
            "dispute over cigarette smoke. The victim underwent emergency surgery at Khoo Teck Puat "
            "Hospital and remains in serious but stable condition. The accused was arrested at the scene."
        ),
        "classification":    "dagger",
        "severity":          4,
        "confidence":        0.92,
        "block_number":      "Block 873",
        "area_name":         "Yishun Ring Road",
        "latitude":          1.4295,
        "longitude":         103.8350,
        "slug":              "yishun-stabbing-noise-dispute-block-873",
        "seo_title":         "Yishun man stabs neighbour 11 times over noise",
        "seo_description":   "A Yishun man stabbed his neighbour 11 times at Block 873 Yishun Ring Road.",
        "pixel_art_prompt":  "16-bit JRPG pixel art, Yishun HDB corridor at night, yellow police tape",
        "tags":              ["stabbing", "assault", "block-873"],
        "hype_meter":        2,
        "chaos_contribution": 12.0,
        "deaths":            None,
        "injuries":          1,
        "source_urls":       ["https://www.channelnewsasia.com/singapore/yishun-stabbing"],
        "edmw_signal_count": 5,
    }

    items = [
        {
            "url":         "https://www.channelnewsasia.com/singapore/yishun-stabbing",
            "title":       "Man stabs neighbour 11 times after noise dispute at Yishun flat",
            "content":     "A 45-year-old man was charged in court on Thursday...",
            "source_name": "CNA",
            "source_type": "msm",
        },
        {
            "url":         "https://www.straitstimes.com/singapore/housing-policy",
            "title":       "Singapore government announces new housing policy",
            "content":     "The government announced updated housing measures today...",
            "source_name": "Straits Times",
            "source_type": "msm",
        },
        {
            "url":         "https://www.channelnewsasia.com/singapore/yishun-stabbing",  # same as item 1
            "title":       "Yishun stabbing victim still hospitalised",
            "content":     "The victim from the Yishun stabbing remains in hospital...",
            "source_name": "Mothership",
            "source_type": "msm",
        },
    ]

    # ── Duplicate tracker ───────────────────────────────────────────────────
    seen_urls: set = set()

    def fake_check_duplicate(url, client=None):
        if url in seen_urls:
            return True
        seen_urls.add(url)
        return False

    # ── Mock Supabase ────────────────────────────────────────────────────────
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.select.return_value.execute.return_value.data = [
        {"id": "mock-queue-uuid-abc123"}
    ]

    print("\nTest items:")
    for i, it in enumerate(items, 1):
        dupe = " [DUPLICATE]" if i == 3 else ""
        print(f"  [{i}] {it['source_name']:15} {it['url']}{dupe}")

    print("\nGraph execution trace:")
    print("-" * 64)

    results = []

    with patch("classifiers.corroboration.check_duplicate", side_effect=fake_check_duplicate), \
         patch("filters.stage1_filter.filter_content",      side_effect=s1_results), \
         patch("filters.stage2_writer.write_stage2",        return_value=s2_mock), \
         patch("orchestrator.herald_agent.check_milestones",
               return_value={"triggered": []}):

        for i, item in enumerate(items, 1):
            initial: GraphState = {
                "item":          item,
                "dry_run":       False,
                "supabase":      mock_sb,
                "s1_result":     None,
                "s2_draft":      None,
                "queue_id":      None,
                "herald_result": None,
                "skipped":       False,
                "skip_reason":   None,
                "error":         None,
                "node_trace":    [],
            }
            final = _app.invoke(initial)
            results.append(final)

            print(f"\nItem {i} [{item['source_name']}] {item['url'][:55]}")
            for step in final.get("node_trace", []):
                print(f"  {step}")

    print("\n" + "-" * 64)
    print("Assertions:")

    # Item 1 (CNA stabbing) — should flow all the way to queue_insert
    assert results[0].get("queue_id") == "mock-queue-uuid-abc123", \
        "FAIL: item 1 not queued"
    assert results[0].get("s1_result", {}).get("passes"), \
        "FAIL: item 1 should pass S1"
    assert results[0].get("s2_draft", {}).get("classification") == "dagger", \
        "FAIL: item 1 should be classified as dagger"

    # Item 2 (generic news) — should be rejected at S1, no queue_id
    assert not results[1].get("s1_result", {}).get("passes"), \
        "FAIL: item 2 should fail S1"
    assert not results[1].get("queue_id"), \
        "FAIL: item 2 should not be queued"

    # Item 3 (duplicate URL) — caught at content_intake
    assert results[2].get("skip_reason") == "duplicate", \
        "FAIL: item 3 should be marked as duplicate"
    assert not results[2].get("s1_result"), \
        "FAIL: item 3 should never reach S1"

    print("  Item 1 (CNA stabbing)  -> S1 pass -> S2 dagger -> queued  OK")
    print("  Item 2 (generic news)  -> S1 reject -> END               OK")
    print("  Item 3 (duplicate URL) -> content_intake -> END           OK")

    print("\n" + "=" * 64)
    print("All assertions passed.")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    import sys as _sys, pathlib as _pathlib, logging as _logging
    # Add packages/agents/ to sys.path so classifiers/filters/orchestrator are importable
    _sys.path.insert(0, str(_pathlib.Path(__file__).parent.parent))
    _logging.basicConfig(level=_logging.WARNING, format="%(levelname)s: %(message)s")
    _run_test()
