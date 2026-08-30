"""
RLS exposure audit — can the browser-safe key read what it must not?

READ-ONLY. Writes nothing, modifies nothing.

This is the programmatic form of the "0a gate" from the 2026-07 cost programme,
which was originally specified as a hand-run `pg_class` query in the Supabase SQL
Editor. Reading `pg_class.relrowsecurity` tells you whether RLS is *enabled*;
this instead tests the thing that actually matters — **whether a client holding
only `SUPABASE_PUBLISHABLE_KEY` can read the rows** — which also catches a table
that has RLS on but a permissive `USING (true)` SELECT policy. A catalog check
passes that case; this does not.

Method: for each table, read once with the SECRET key (bypasses RLS, so it
reports what is really there) and once with the PUBLISHABLE key. A table is
EXPOSED when the publishable key returns rows it should not see.

`incidents` is included as a control. CLAUDE.md documents anon reads as filtered
to `is_published = TRUE`, so the control both proves the method works and
confirms that drafts are invisible to the public key.

Run:
    ./.venv/Scripts/python.exe tools/rls_audit.py
Exit code 1 if anything is exposed, so it can gate a deploy.
"""

import pathlib
import sys

_AGENTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _AGENTS_ROOT.parents[1]
sys.path.insert(0, str(_AGENTS_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env", override=False)

import os  # noqa: E402

# The six ops tables added by migration 011. None of these should ever be
# readable by the publishable key: they carry run stats, the operator's
# Telegram chat id, cost estimates and internal health state.
OPS_TABLES = [
    "agent_runs",
    "agent_events",
    "notifications",
    "learning_snapshots",
    "monthly_reports",
    "backend_health_checks",
]

# Tables the public site legitimately reads, with the rule that should apply.
CONTROLS = [
    ("incidents", "published rows only (anon_read_published_incidents)"),
]

# Never public: operator review state and the learning signal behind it.
# pattern_alerts/people_profiles joined after the July-2026 audit found
# migration 003 had left them with PUBLIC USING(true) policies (fixed in 013)
# — people_profiles especially must never leak (names, aliases,
# legal_sensitivity on unpublished profiles).
ALSO_CHECK = [
    "war_room_queue",
    "training_signals",
    "source_reputation",
    "pattern_alerts",
    "people_profiles",
]


def _client(key_env: str):
    from supabase import create_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv(key_env)
    if not url or not key:
        raise EnvironmentError(f"SUPABASE_URL and {key_env} must be set")
    return create_client(url, key)


def _count(client, table: str):
    """(rows_visible, note). Never raises — a refusal IS the result we want."""
    try:
        res = client.table(table).select("*", count="exact").limit(1).execute()
        return (res.count if res.count is not None else len(res.data or [])), ""
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        return 0, f"refused ({msg[:60]}…)" if len(msg) > 60 else f"refused ({msg})"


def main() -> int:
    try:
        secret = _client("SUPABASE_SECRET_KEY")
        public = _client("SUPABASE_PUBLISHABLE_KEY")
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}")
        return 1

    print("RLS exposure audit — publishable key vs secret key")
    print("=" * 78)
    print(f"{'table':<26}{'secret':>9}{'publishable':>13}   verdict")
    print("-" * 78)

    exposed = []

    for table in OPS_TABLES + ALSO_CHECK:
        s, _ = _count(secret, table)
        p, note = _count(public, table)
        if p > 0:
            verdict = f"!! EXPOSED — {p} row(s) readable with the browser-safe key"
            exposed.append((table, p))
        elif s == 0:
            verdict = "protected (table empty — weak evidence)"
        else:
            verdict = "protected" + (f" — {note}" if note else "")
        print(f"{table:<26}{s:>9}{p:>13}   {verdict}")

    print()
    for table, rule in CONTROLS:
        s, _ = _count(secret, table)
        p, _ = _count(public, table)
        try:
            pub_true = secret.table(table).select("id", count="exact") \
                .eq("is_published", True).limit(1).execute().count
        except Exception:  # noqa: BLE001
            pub_true = None
        ok = (pub_true is not None and p == pub_true and p < s)
        print(f"CONTROL {table}: secret sees {s}, publishable sees {p}, "
              f"published={pub_true} -> {'as documented' if ok else 'UNEXPECTED'}")
        print(f"  rule: {rule}")
        if ok:
            print("  (the control behaving correctly is what makes the results above trustworthy)")

    # ── Second half of the 0a gate: is migration 011 actually applied? ──────
    # Without it, `decided_by` does not exist and `auto_approve` violates the
    # training_signals.action CHECK — so every autonomous decision insert is
    # silently rejected while every publish succeeds. Live incidents nobody
    # approved, and no record that an agent chose them.
    #
    # Proven from DATA rather than by a trial insert: a row that EXISTS carrying
    # a post-011 value is proof the constraint accepts it, and costs no write.
    print()
    print("Migration 011 (autonomy/ops schema) — evidence from existing rows:")
    ok_011 = True
    try:
        res = secret.table("training_signals").select("action,decided_by").execute()
        rows = res.data or []
        actions = {r.get("action") for r in rows}
        decided = {r.get("decided_by") for r in rows}
        has_col = any("decided_by" in r for r in rows)
        print(f"  decided_by column present     : {has_col}  (values seen: {sorted(d for d in decided if d)})")
        n_auto = sum(1 for r in rows if r.get("action") == "auto_approve")
        print(f"  action='auto_approve' rows    : {n_auto}  "
              f"-> {'CHECK accepts it (pre-011 would reject)' if n_auto else 'none yet — inconclusive'}")
        n_rev = sum(1 for r in rows if r.get("action") == "auto_publish_reverted")
        print(f"  action='auto_publish_reverted': {n_rev}  "
              f"({'observed' if n_rev else 'none yet — expected, it is a rare path'})")
        ok_011 = has_col and (n_auto > 0 or n_rev > 0)
        print(f"  verdict: {'011 APPLIED' if ok_011 else 'INCONCLUSIVE — no autonomous decision has been recorded yet'}")
        if not ok_011 and has_col:
            print("           (decided_by exists, so 011 almost certainly ran; the CHECK")
            print("            half is simply unproven until auto-publish records a decision)")
    except Exception as exc:  # noqa: BLE001
        ok_011 = False
        print(f"  could not read training_signals: {exc}")

    print()
    print("=" * 78)
    if exposed:
        print(f"FAIL — {len(exposed)} table(s) readable with the publishable key:")
        for t, n in exposed:
            print(f"  {t}: {n} row(s)")
        print("\nApply migration 011's RLS block before deploying.")
        return 1
    print("PASS — no ops table is readable with the publishable key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
