"""
Stage-2 WRITE model eval: claude-sonnet-4-6 vs claude-haiku-4-5 (programme 3.1).

Measures whether Haiku can replace Sonnet as MODEL_WRITE, BEFORE anything in the
pipeline changes. Writes no pipeline code and does not modify stage2_writer.py:
the model is swapped by rebinding the module global MODEL_WRITE around each
batch, which _write_draft reads at call time. Production behaviour is untouched.

Input is the frozen fixture (tools/dump_l2_eval_set.py), never the live queue, so
runs are reproducible and the two models see byte-identical inputs.

Outputs (all under tools/eval_out/):
  l2_eval.csv          per-output metrics, real model names — the numbers
  l2_eval_blind.md     side-by-side A/B with model identity REDACTED — for scoring
  l2_eval_key.json     which of A/B was which model, per item — open AFTER scoring

THE GATE METRIC is ungrounded specifics: numbers and capitalised multi-word
proper nouns in the summary that appear nowhere in the source bodies. A smaller
model degrading on merged multi-source prose shows up here first, as invented
block numbers, ages and agency names.

Caveat, stated so the number is read correctly: the proper-noun detector is
lexical and over-triggers on things like a capitalised phrase the model rewrote
("Yishun Ring Road" vs source "Yishun ring road"). That bias is IDENTICAL for
both models, so the comparison stands even though the absolute count is a
ceiling, not a true invention count. Read the deltas, and the listed strings.

Run:
    ./.venv/Scripts/python.exe tools/eval_l2_write.py
"""

import csv
import json
import pathlib
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor

_AGENTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENTS_ROOT.parents[1]
sys.path.insert(0, str(_AGENTS_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env", override=False)

from filters import stage2_writer as sw  # noqa: E402

FIXTURE = _AGENTS_ROOT / "fixtures" / "l2_eval_set.json"
OUT_DIR = _AGENTS_ROOT / "tools" / "eval_out"

MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}
WORKERS = 4
BLIND_SEED = 20260730          # fixed so the blind assignment is reproducible

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Two or more consecutive capitalised words — "Khoo Teck Puat", "Yishun Ring Road".
_PROPER_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[A-Z]{2,}))+\b")
_SLUG_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)-(?:19|20)\d{2}$"
)


# ── Source text the draft must be grounded in ───────────────────────────────

def source_text(inp: dict) -> str:
    """Concatenated NON-SIGNAL source bodies + titles. Guardrail #2: signals excluded."""
    parts = [inp.get("title", ""), inp.get("content", "")]
    for art in inp.get("source_articles") or []:
        if art.get("source_type") == "signal":
            continue
        parts.append(art.get("title", ""))
        parts.append(art.get("content", ""))
    return "\n".join(p for p in parts if p)


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def ungrounded(summary: str, src: str) -> tuple[list[str], list[str]]:
    """(ungrounded numbers, ungrounded proper nouns) present in summary but not src."""
    src_nums = {_norm_num(m.group()) for m in _NUM_RE.finditer(src)}
    src_low = src.lower()

    bad_nums = []
    for m in _NUM_RE.finditer(summary):
        n = _norm_num(m.group())
        if n and n not in src_nums:
            bad_nums.append(n)

    bad_nouns = []
    for m in _PROPER_RE.finditer(summary):
        phrase = m.group().strip()
        if phrase.lower() not in src_low:
            bad_nouns.append(phrase)

    return sorted(set(bad_nums)), sorted(set(bad_nouns))


def score(inp: dict, draft: dict) -> dict:
    summary = draft.get("summary", "") or ""
    title = draft.get("title", "") or ""
    slug = draft.get("slug", "") or ""
    seo_t = draft.get("seo_title", "") or ""
    seo_d = draft.get("seo_description", "") or ""
    nums, nouns = ungrounded(summary, source_text(inp))
    return {
        "summary_chars":     len(summary),
        "ungrounded_nums":   len(nums),
        "ungrounded_nouns":  len(nouns),
        "ungrounded_total":  len(nums) + len(nouns),
        "ungrounded_list":   "; ".join(nums + nouns),
        "slug_ok":           bool(_SLUG_RE.match(slug)),
        "slug":              slug,
        "title_has_yishun":  "yishun" in title.lower(),
        "title_chars":       len(title),
        "title_ok":          "yishun" in title.lower() and len(title) <= 120,
        "seo_title_chars":   len(seo_t),
        "seo_title_ok":      len(seo_t) <= 60,
        "seo_desc_chars":    len(seo_d),
        "seo_desc_ok":       len(seo_d) <= 155,
        "political":         bool(draft.get("political", False)),
        "confidence":        draft.get("confidence"),
    }


# ── Run ──────────────────────────────────────────────────────────────────────

def run_model(key: str, model_id: str, inputs: list[dict]) -> list[dict | None]:
    """
    Run every fixture input through write_stage2 on `model_id`.

    The global is rebound ONCE per batch, not per call: _write_draft reads
    sw.MODEL_WRITE at call time, so patching it per-thread would race.
    """
    original = sw.MODEL_WRITE
    sw.MODEL_WRITE = model_id
    print(f"\n[{key}] MODEL_WRITE={model_id} — {len(inputs)} input(s), {WORKERS} workers")
    try:
        def one(idx_inp):
            i, inp = idx_inp
            payload = {k: v for k, v in inp.items() if not k.startswith("_")}
            try:
                d = sw.write_stage2(payload)
                print(f"  [{key}] {i + 1}/{len(inputs)} ok", flush=True)
                return d
            except Exception as exc:  # noqa: BLE001
                print(f"  [{key}] {i + 1}/{len(inputs)} FAILED: {exc}", flush=True)
                return None

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            return list(pool.map(one, enumerate(inputs)))
    finally:
        sw.MODEL_WRITE = original


def summarise(rows: list[dict], half: str) -> dict:
    sel = [r for r in rows if r["half"] == half]
    if not sel:
        return {}
    n = len(sel)
    def mean(k):  return sum(r[k] for r in sel) / n
    def pct(k):   return 100.0 * sum(1 for r in sel if r[k]) / n
    return {
        "n": n,
        "summary_chars": mean("summary_chars"),
        "ungrounded_total": sum(r["ungrounded_total"] for r in sel),
        "ungrounded_per_draft": mean("ungrounded_total"),
        "drafts_with_ungrounded": pct("ungrounded_total") if False else
                                  100.0 * sum(1 for r in sel if r["ungrounded_total"] > 0) / n,
        "slug_ok_pct": pct("slug_ok"),
        "title_ok_pct": pct("title_ok"),
        "seo_title_ok_pct": pct("seo_title_ok"),
        "seo_desc_ok_pct": pct("seo_desc_ok"),
        "political_flags": sum(1 for r in sel if r["political"]),
    }


def main() -> int:
    if not FIXTURE.exists():
        print(f"FATAL: fixture missing — run tools/dump_l2_eval_set.py first ({FIXTURE})")
        return 1
    inputs = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(f"Loaded {len(inputs)} fixture input(s) from {FIXTURE.name}")
    print(f"  single-source: {sum(1 for i in inputs if i.get('_label') == 'single')}")
    print(f"  multi-source : {sum(1 for i in inputs if i.get('_label') == 'multi')}")

    drafts = {k: run_model(k, m, inputs) for k, m in MODELS.items()}

    rows = []
    for k in MODELS:
        for i, (inp, d) in enumerate(zip(inputs, drafts[k])):
            if d is None:
                continue
            rows.append({
                "item": i,
                "half": inp.get("_label", "?"),
                "sources": len(inp.get("source_urls") or []),
                "model": k,
                **score(inp, d),
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "l2_eval.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ── Blind side-by-side + key ────────────────────────────────────────────
    rng = random.Random(BLIND_SEED)
    key_map, md = {}, ["# Stage 2 write eval — blind A/B",
                       "",
                       "Model identities are redacted. Score first, then open "
                       "`l2_eval_key.json`.", ""]
    for i, inp in enumerate(inputs):
        pair = list(MODELS.keys())
        rng.shuffle(pair)
        key_map[str(i)] = {"A": pair[0], "B": pair[1],
                           "half": inp.get("_label"),
                           "sources": len(inp.get("source_urls") or [])}
        md.append(f"## Item {i} — {inp.get('_label')} "
                  f"({len(inp.get('source_urls') or [])} source(s))")
        md.append(f"**Input title:** {inp.get('title', '')[:140]}")
        md.append("")
        for slot, mk in (("A", pair[0]), ("B", pair[1])):
            d = drafts[mk][i]
            if d is None:
                md.append(f"### {slot}\n\n_(generation failed)_\n")
                continue
            s = d.get("summary", "")
            md.append(f"### {slot}")
            md.append(f"- **title:** {d.get('title', '')}")
            md.append(f"- **slug:** {d.get('slug', '')}")
            md.append(f"- **seo_title:** {d.get('seo_title', '')}")
            md.append(f"- **summary ({len(s)} chars):**")
            md.append("")
            md.append(f"> {s}")
            md.append("")
        md.append("---\n")

    md_path = OUT_DIR / "l2_eval_blind.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    key_path = OUT_DIR / "l2_eval_key.json"
    key_path.write_text(json.dumps(key_map, indent=2), encoding="utf-8")

    # ── Summary table ───────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("SUMMARY  (the gate metric is ungrounded specifics; multi-source is the half that matters)")
    print("=" * 100)
    hdr = (f"{'half':<7} {'model':<7} {'n':>3} {'chars':>7} {'ungrnd':>7} {'/draft':>7} "
           f"{'%dirty':>7} {'slug%':>6} {'title%':>7} {'seoT%':>6} {'seoD%':>6} {'pol':>4}")
    for half in ("single", "multi"):
        print(f"\n{hdr}")
        print("-" * 100)
        for k in MODELS:
            s = summarise([r for r in rows if r["model"] == k], half)
            if not s:
                continue
            print(f"{half:<7} {k:<7} {s['n']:>3} {s['summary_chars']:>7.0f} "
                  f"{s['ungrounded_total']:>7} {s['ungrounded_per_draft']:>7.2f} "
                  f"{s['drafts_with_ungrounded']:>6.0f}% {s['slug_ok_pct']:>5.0f}% "
                  f"{s['title_ok_pct']:>6.0f}% {s['seo_title_ok_pct']:>5.0f}% "
                  f"{s['seo_desc_ok_pct']:>5.0f}% {s['political_flags']:>4}")

    print("\nUngrounded specifics, listed (multi-source half only):")
    for k in MODELS:
        items = [r for r in rows if r["model"] == k and r["half"] == "multi" and r["ungrounded_list"]]
        print(f"\n  [{k}]  {len(items)} draft(s) with at least one:")
        for r in items:
            print(f"    item {r['item']:>2} ({r['sources']} src): {r['ungrounded_list'][:150]}")

    print(f"\nWrote:\n  {csv_path}\n  {md_path}   <- score this blind\n  {key_path}  <- open after scoring")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
