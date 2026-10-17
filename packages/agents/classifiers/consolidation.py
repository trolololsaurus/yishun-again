"""
Backward-compat re-export shim.

The consolidation agent (check(), ConsolidationResult) moved to consolidation/check.py (INGESTION_DESIGN.md
§10b step 2) so it can be shared between backfill_agent.py and the future
ingestion/orchestrator.py. pipeline.py continues to import from here.
"""

from consolidation.check import (
    ConsolidationResult,
    check,
)

__all__ = ["ConsolidationResult", "check"]
