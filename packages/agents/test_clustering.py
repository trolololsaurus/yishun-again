"""
Story clustering (gather -> cluster -> write-once). Pure, offline.

Run: .venv/Scripts/python.exe test_clustering.py

The load-bearing property is the MERGE asymmetry: it must group same-story
sources, but must NEVER merge two different events (a wrong merge corrupts a
published incident; a wrong split is a cosmetic miss). Signal members must ATTACH
to at most one cluster, never bridge two.
"""
import importlib
from datetime import date

cl = importlib.import_module("ingestion.clustering")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


class C:
    """Candidate stand-in (duck-typed on the attrs clustering reads)."""
    def __init__(self, title, content="", url="", source_type="msm",
                 published_at=None, source_name="src"):
        self.title, self.content, self.url = title, content, url
        self.source_type, self.published_at, self.source_name = source_type, published_at, source_name


def item_of(c):
    return {"title": c.title, "content": c.content, "url": c.url,
            "date": c.published_at.isoformat() if c.published_at else "",
            "source_name": c.source_name, "source_type": c.source_type}


def roots(clusters):
    """Set of frozensets of titles, for order-independent comparison."""
    return {frozenset(m.title for m in cl_) for cl_ in clusters}


print("clustering — grouping:\n")

# 1. Same story, two MSM outlets, dates a day apart -> one cluster.
a = C("Man stabbed at Yishun Block 873 wet market", "stabbing dispute cigarette", "u/cna", published_at=date(2026, 7, 1))
b = C("Yishun wet market stabbing over cigarette smoke", "man stabbed block 873", "u/msn", published_at=date(2026, 7, 2))
cs = cl.cluster_candidates([a, b])
check("same story, 2 outlets, 1 day apart -> merged", len(cs) == 1 and len(cs[0]) == 2)

# 2. Different stories, low overlap -> two clusters.
x = C("Cat rescued from Yishun void deck drain", "kitten spca rescue", "u/1", published_at=date(2026, 7, 1))
y = C("Lift breakdown traps elderly man at Yishun block", "lift malfunction trapped", "u/2", published_at=date(2026, 7, 1))
cs = cl.cluster_candidates([x, y])
check("different stories -> not merged", len(cs) == 2)

# 3. Same keywords but 10 days apart -> NOT merged (date window).
p = C("Fire breaks out at Yishun coffeeshop kitchen", "coffeeshop kitchen fire scdf", "u/a", published_at=date(2026, 7, 1))
q = C("Fire breaks out at Yishun coffeeshop kitchen", "coffeeshop kitchen fire scdf", "u/b", published_at=date(2026, 7, 15))
cs = cl.cluster_candidates([p, q])
check("same words, 10 days apart -> separate events", len(cs) == 2, f"-> {len(cs)}")

# 4. A third same-day outlet joins the pair -> cluster of 3.
c3 = C("Cigarette-smoke dispute stabbing at Yishun market Block 873", "stabbing wet market", "u/st", published_at=date(2026, 7, 1))
cs = cl.cluster_candidates([a, b, c3])
check("third same-day outlet joins -> cluster of 3", len(cs) == 1 and len(cs[0]) == 3)

print("\nclustering — signal attach, never bridge:\n")

# 5. A reddit signal about the stabbing attaches to the MSM cluster.
r = C("[r/singapore] Anyone know about the Yishun market stabbing block 873?", "stabbing market", "reddit.com/x",
      source_type="signal", published_at=date(2026, 7, 3))
cs = cl.cluster_candidates([a, b, r])
check("reddit signal attaches to its MSM story", len(cs) == 1 and len(cs[0]) == 3)

# 6. A signal overlapping TWO distinct MSM clusters attaches to ONE, never bridges.
fire1 = C("Fire at Yishun Block 100 kitchen", "kitchen fire block 100 scdf", "u/f1", published_at=date(2026, 7, 1))
fire2 = C("Kitchen fire at Yishun Block 900 flat", "kitchen fire block 900 scdf", "u/f2", published_at=date(2026, 7, 20))
# distinct events (19 days apart, different blocks) -> two clusters
base = cl.cluster_candidates([fire1, fire2])
sig = C("[reddit] scary kitchen fire in Yishun again", "kitchen fire yishun", "reddit.com/y",
        source_type="signal", published_at=date(2026, 7, 10))
cs = cl.cluster_candidates([fire1, fire2, sig])
check("two distinct fire events stay separate", len(base) == 2)
check("signal does NOT bridge two events (still 2 clusters)", len(cs) == 2, f"-> {len(cs)}")
check("signal joined exactly one cluster",
      sum(1 for c_ in cs if sig in c_) == 1 and sorted(len(c_) for c_ in cs) == [1, 2])

# 7. Single candidate -> single cluster of one.
cs = cl.cluster_candidates([a])
check("one candidate -> one cluster of one", len(cs) == 1 and len(cs[0]) == 1)
check("empty input -> empty", cl.cluster_candidates([]) == [])

# 8. Dateless MSM member stays STANDALONE — never keyword-merged without an
#    event date to gate it (an unconfirmed merge of a real source is the exact
#    false-merge risk; only signals attach unconfirmed).
dateless = C("Yishun market stabbing latest", "stabbing market block 873", "u/dl", published_at=None)
cs = cl.cluster_candidates([a, b, dateless])
check("dated pair still merges; dateless MSM stays its own cluster",
      len(cs) == 2 and sorted(len(x) for x in cs) == [1, 2])

print("\nbuild_cluster_stage2_input:\n")

# multi-source cluster -> one stage2 input with all non-signal sources
inp = cl.build_cluster_stage2_input([a, b, c3, r], item_of)
check("source_urls = the 3 non-signal members (reddit excluded)",
      sorted(inp["source_urls"]) == sorted(["u/cna", "u/msn", "u/st"]),
      f"-> {inp['source_urls']}")
check("reddit URL never in source_urls (guardrail #2)", "reddit.com/x" not in inp["source_urls"])
check("edmw_signal_count counts the signal member", inp["edmw_signal_count"] == 1)
check("primary = earliest-dated non-signal (Jul 1)", inp.get("date") == "2026-07-01", f"-> {inp.get('date')}")
check("multi-source attaches source_articles", len(inp.get("source_articles", [])) == 3)
check("source_articles excludes the signal member",
      all("reddit" not in art["url"] for art in inp["source_articles"]))

# single-member cluster -> byte-identical to the per-candidate path (no source_articles)
solo = cl.build_cluster_stage2_input([a], item_of)
check("single-member cluster carries no source_articles (byte-identical path)",
      "source_articles" not in solo)
check("single-member source_urls = [its url]", solo["source_urls"] == ["u/cna"])

# signal-only cluster -> empty source_urls (guardrail #1 keeps it in the queue)
sig_only = cl.build_cluster_stage2_input([r], item_of)
check("signal-only cluster -> source_urls empty (never auto-publishes)", sig_only["source_urls"] == [])
check("signal-only cluster -> edmw_signal_count 1", sig_only["edmw_signal_count"] == 1)

print("\nsummarize + oversized:\n")
summary = cl.summarize(cl.cluster_candidates([a, b, c3, x]))
check("summarize counts a saved-draft estimate",
      summary["sonnet_drafts_saved_estimate"] == 2 and summary["multi_member_clusters"] == 1,
      f"-> {summary}")
big = [C(f"Yishun stabbing market block 873 report {i}", "stabbing market block 873", f"u/{i}",
         published_at=date(2026, 7, 1)) for i in range(8)]
cs = cl.cluster_candidates(big)
check("8 identical-story members cluster together", len(cs) == 1 and len(cs[0]) == 8)
check("oversized() flags a cluster past the cap", cl.oversized(cs[0], max_size=6))

print("\ncluster_with_confirmation (LLM-gated merges):\n")

# The keyword pre-filter proposes a and b (same story) AND a generic-token bridge
# to an unrelated same-window event; the judge confirms only the real one.
bridge = C("Fire at Yishun coffeeshop stall", "coffeeshop fire block 873", "u/br", published_at=date(2026, 7, 1))


def judge_same_story(x, y):
    # 'same event' only when both are the market stabbing.
    stab = lambda c_: "stab" in (c_.title + c_.content).lower()
    return stab(x) and stab(y)


clusters, stats = cl.cluster_with_confirmation([a, b, bridge], judge_same_story)
check("confirmed merge keeps a+b together", any(len(c_) == 2 for c_ in clusters))
check("unconfirmed bridge (coffeeshop fire) stays separate",
      any(len(c_) == 1 and c_[0] is bridge for c_ in clusters), f"-> {[[m.title[:20] for m in c_] for c_ in clusters]}")
check("stats report a confirmed edge", stats["edges_confirmed"] == 1 and stats["edges_judged"] >= 1)


def judge_never(x, y):
    return False


clusters, stats = cl.cluster_with_confirmation([a, b, c3], judge_never)
check("judge rejects everything -> all split (split-safe default)", len(clusters) == 3)


def judge_boom(x, y):
    raise RuntimeError("model down")


clusters, stats = cl.cluster_with_confirmation([a, b], judge_boom)
check("judge error -> no merge, no raise", len(clusters) == 2 and stats["judge_errors"] >= 1)

# judge cap bounds the number of LLM calls
calls = {"n": 0}
def judge_count(x, y):
    calls["n"] += 1
    return False
many = [C(f"Yishun stabbing market {i}", "stabbing market wet", f"u/{i}", published_at=date(2026, 7, 1)) for i in range(10)]
clusters, stats = cl.cluster_with_confirmation(many, judge_count, max_judges=5)
check("judge calls are capped at max_judges", calls["n"] <= 5, f"-> {calls['n']}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
