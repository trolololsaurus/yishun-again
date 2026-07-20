"""
ops/ — the autonomy support layer.

Everything here exists so the daily unattended pass is observable and
accountable. Nothing in this package is allowed to break the pipeline: an
observability layer that can crash the thing it observes is worse than no
observability layer, because it converts a logging outage into a data outage.

Every public entry point in this package therefore swallows its own exceptions
and degrades to stdlib logging.

  activity.py       agent_runs / agent_events — what every agent did (req #7)
  notify.py         outbound email + dedup ledger (reqs #4, #9, #11, #12)
  supervisor.py     watches the scraping fleet, mails on serious anomaly (req #9)
  maintenance.py    plain-English digest of what broke + how to fix it (req #11)
  backend_health.py component health + cost guard (req #12)
  integrity.py      duplicate entries + hallucination signals (req #10)
  monthly_report.py 30-day orchestrator summary, with history (req #13)

Every agent here exposes `run(supabase_client=None) -> dict`, matching the
convention of the existing agents (classifiers/lifecycle.py). All of them run
unattended, so they return a stats dict instead of raising.
"""
